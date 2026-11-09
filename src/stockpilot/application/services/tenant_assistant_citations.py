"""Cite the authorized transcript's actual sources, never a new semantic guess."""
from copy import deepcopy

from stockpilot.application.services.tenant_rag_safety import (
    RAG_CITATION_REFUSAL, RAG_SENSITIVE_REFUSAL, safe_public,
)
from stockpilot.application.use_cases.catalogue_advice import normalize

NO_SOURCE = "Aucune source documentaire vérifiable n’est associée à la réponse précédente de cette conversation."
SOURCE_UNAVAILABLE = "La source documentaire historique n’est plus vérifiable ou accessible. Son contenu et sa citation ne sont pas reproduits."
EMPTY_RAG = "Aucun passage documentaire compatible n’est disponible dans votre organisation."


def citation_question(question):
    return normalize(question).strip().rstrip('.?!').strip() == "cite la source documentaire de ta reponse si elle existe"


class TenantAssistantCitations:
    def __init__(self, store, retrieval):
        self.store, self.retrieval = store, retrieval

    @staticmethod
    def scope(context, conversation_id):
        return {'version': 1, 'organization_id': context.organization_id,
                'owner_user_id': context.principal.user_id, 'conversation_id': conversation_id}

    @staticmethod
    def absent(message=NO_SOURCE):
        return {'intent': 'RAG_CITATION', 'text': message, 'products': [],
                'order': None, 'can_confirm': False, 'cited_sources': [], 'source_turn_id': None}

    def _verified(self, request, context, conversation_id, turn):
        reply = turn.get('reply') or {}
        scope = reply.get('_citation_scope')
        if (turn.get('status') != 'completed' or not safe_public(reply)
                or not isinstance(scope, dict) or type(scope.get('version')) is not int
                or scope != self.scope(context, conversation_id)):
            return False
        refs = reply.get('cited_sources')
        source_id = reply.get('source_turn_id')
        if not isinstance(refs, list) or not self._turn_id(source_id):
            return False
        if reply.get('intent') == 'RAG':
            if source_id != turn.get('turn_id'):
                return False
            read_refs = reply.get('_rag_read_sources')
            if not isinstance(read_refs, list) or not read_refs:
                return False
            if self.retrieval.revalidate_citations(request, context, read_refs) != read_refs:
                return False
            if any(ref not in read_refs for ref in refs):
                return False
        elif reply.get('intent') == 'RAG_CITATION':
            if source_id == turn.get('turn_id'):
                return False
            original = self.store.turn(request, context, conversation_id, source_id)
            if (not isinstance(original, dict) or (original.get('reply') or {}).get('intent') != 'RAG'
                    or not self._verified(request, context, conversation_id, original)
                    or original['reply'].get('cited_sources') != refs):
                return False
        else:
            return False
        return not refs or self.retrieval.revalidate_citations(request, context, refs) == refs

    def previous(self, request, context, conversation_id, turn_id):
        # The journal is authoritative even after checkpoint loss or an upgrade.
        # Inspect the current turn and two predecessors to detect equal-time
        # ambiguity. Never skip a
        # catalogue/action/uncited reply to resurrect older documentary context.
        page = self.store.history(request, context, conversation_id, 3, 0)
        turns = [t for t in page['items'] if t.get('turn_id') != turn_id]
        if not turns or turns[-1].get('status') != 'completed':
            return self.absent()
        if len(turns) > 1 and turns[-1].get('created_at') == turns[-2].get('created_at'):
            return self.absent()
        previous = self.store.turn(request, context, conversation_id, turns[-1]['turn_id'])
        if not isinstance(previous, dict):
            return self.absent()
        reply = previous.get('reply') or {}
        if not reply.get('cited_sources'):
            return self.absent()
        if not self._verified(request, context, conversation_id, previous):
            return self.absent(SOURCE_UNAVAILABLE)
        refs = deepcopy(reply['cited_sources'])
        return {'intent': 'RAG_CITATION', 'text': self.retrieval.render_citations(refs),
                'products': [], 'order': None, 'can_confirm': False,
                'source_turn_id': reply['source_turn_id'], 'cited_sources': refs,
                '_citation_scope': self.scope(context, conversation_id)}

    @staticmethod
    def _turn_id(value):
        return isinstance(value, str) and 0 < len(value) <= 128 and value == value.strip() and '\x00' not in value

    def public_reply(self, request, context, conversation_id, turn):
        reply = turn.get('reply')
        if not isinstance(reply, dict):
            return reply
        if reply.get('intent') == 'HISTORY' and reply.get('historical_turn_id'):
            # G14-05 copies a snapshot, not continuing access rights. Its public
            # replay must follow the authorized original turn after revocation.
            identity = reply['historical_turn_id']
            if not conversation_id or not self._turn_id(identity) or identity == turn.get('turn_id'):
                return self.absent(SOURCE_UNAVAILABLE)
            original = self.store.turn(request, context, conversation_id, identity)
            if not isinstance(original, dict):
                return self.absent(SOURCE_UNAVAILABLE)
            if (original.get('reply') or {}).get('intent') in {'RAG', 'RAG_CITATION'}:
                if not self._verified(request, context, conversation_id, original):
                    return self.absent(SOURCE_UNAVAILABLE)
        if reply.get('intent') not in {'RAG', 'RAG_CITATION'}:
            return reply
        if (reply.get('text') in {NO_SOURCE, SOURCE_UNAVAILABLE, EMPTY_RAG,
                                 RAG_SENSITIVE_REFUSAL, RAG_CITATION_REFUSAL}
                and not reply.get('cited_sources') and not reply.get('_rag_read_sources')):
            return {k: deepcopy(v) for k,v in reply.items()
                    if k not in {'_citation_scope', '_rag_read_sources'}}
        if not conversation_id or not self._verified(request, context, conversation_id, turn):
            return self.absent(SOURCE_UNAVAILABLE)
        return {k: deepcopy(v) for k,v in reply.items()
                if k not in {'_citation_scope', '_rag_read_sources'}}
