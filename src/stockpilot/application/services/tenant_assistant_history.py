"""Bounded private history reads. Stored observations are never current stock."""

import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta

from stockpilot.application.use_cases.catalogue_advice import ClarificationRequired, normalize


def history_kind(question):
    value = normalize(question).strip().rstrip('.?!').strip()
    return {
        "qu'est-ce qu'on avait recherche dans cette conversation": "searches",
        "montre mes cinq derniers messages": "messages",
        "compare avec le premier produit de notre conversation": "compare",
        "je veux voir la reponse historique telle qu'elle etait": "snapshot",
        "actualise les stocks de cette ancienne reponse": "refresh",
        "la reponse d'hier affiche 5 unites ; est-ce encore vrai": "yesterday",
    }.get(value)


def abbreviated_preparation(question):
    """B2B-only alias: retain the entire literal count and SKU evidence.

    The shared ORDER parser remains authoritative for invalid counts. No
    trailing clause, inferred product, implicit count or model hint is accepted.
    """
    match = re.fullmatch(
        r"\s*pr[eé]pare(?:z)?[- ]moi\s+([^\s]+\s+unit[eé]s?\s+de\s+"
        r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+)[.!?]?\s*", question, re.I,
    )
    return "Prépare une commande de " + match[1] if match else None


def confirmation_guidance(question):
    return normalize(question).strip().rstrip('.?!').strip() == "confirme definitivement la reservation"


def stale_preparation_question(question):
    return normalize(question).strip().rstrip('.?!').strip() == "la quantite a change depuis ma preparation, que faire"


class TenantAssistantHistory:
    def __init__(self, store, catalogue, documentary_reply=None):
        self.store, self.catalogue = store, catalogue
        self.documentary_reply = documentary_reply

    def _turns(self, request, context, conversation_id, turn_id):
        turns = []
        # The persisted conversation itself is limited to 100 turns. Every
        # page repeats the store's principal, session, membership and owner check.
        for offset in range(0, 100, 20):
            page = self.store.history(request, context, conversation_id, 20, offset)
            turns = list(page['items']) + turns
            if not page['has_more']:
                break
        return [t for t in turns if t['turn_id'] != turn_id and t['status'] == 'completed']

    @staticmethod
    def _snapshots(turns):
        return [t for t in turns if t.get('reply') and t['reply'].get('text')
                and t['reply'].get('intent') not in {'HISTORY', 'CLARIFICATION_REQUIRED', 'CONFIRMATION_REQUIRED'}
                and not t['reply'].get('historical_turn_id')]

    def _current_products(self, request, context, cards):
        if not 1 <= len(cards) <= 5:
            raise ClarificationRequired("Précisez une réponse contenant au plus cinq produits.")
        result = []
        for card in cards:
            product = self.catalogue.catalogue.by_sku(request, context, card['sku'])
            if product is None or product.status != 'active' or product.product_id != card['product_id']:
                raise ClarificationRequired("Une ancienne référence n’est plus accessible ou a changé d’identité. Relancez une recherche autorisée.")
            result.append(product)
        return result

    def execute(self, request, context, conversation_id, state):
        kind = history_kind(state['question'])
        turns = self._turns(request, context, conversation_id, state['turn_id'])
        if kind in {'messages', 'searches'}:
            messages = [t['input'].get('message', '') for t in turns if t['kind'] == 'message'
                        and (kind == 'messages' or (t.get('reply') or {}).get('intent') in
                             {'STOCK_CHECK', 'LOW_STOCK', 'PRODUCT_ADVICE', 'COMPARISON', 'CATALOGUE_NAVIGATION', 'CATALOGUE_CATEGORIES'})]
            if not messages:
                raise ClarificationRequired("Aucun message historique correspondant dans cette conversation autorisée.")
            excerpts = [m if len(m) <= 1000 else m[:1000] + ' [extrait tronqué]' for m in messages[-5:]]
            return {'intent': 'HISTORY', 'text': "Historique de cette conversation uniquement :\n" + '\n'.join(f'{i}. {m}' for i, m in enumerate(excerpts, 1))}
        snapshots = self._snapshots(turns)
        if kind == 'compare':
            snapshots = [t for t in snapshots if t['reply'].get('products')]
            if not snapshots:
                raise ClarificationRequired("Aucun premier produit vérifiable dans cette conversation.")
            first = snapshots[0]['reply']['products'][0]
            latest = snapshots[-1]['reply']['products']
            skus = ([latest[0]['sku']] if len(snapshots) > 1 and len(latest) == 1
                    else list(dict.fromkeys(state.get('advice_skus') or [])))
            if len(skus) != 1 or skus[0] == first['sku']:
                raise ClarificationRequired("Précisez le SKU à comparer au premier produit de cette conversation.")
            other_card = next((p for t in reversed(snapshots) for p in t['reply']['products'] if p['sku'] == skus[0]), None)
            if other_card is None:
                raise ClarificationRequired("La référence à comparer n’est pas identifiable dans cette conversation. Précisez un SKU autorisé.")
            products = self._current_products(request, context, [first, other_card])
            return {'intent': 'COMPARISON', 'text': "Comparaison actuelle avec le premier produit historique : prix, devises et stocks revérifiés ; aucune qualité ni performance supposée.",
                    'products': [self.catalogue.card(request, context, p) for p in products],
                    'historical_turn_id': snapshots[0]['turn_id'], 'advice_skus': [p.sku for p in products]}
        if kind == 'yesterday':
            yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
            def matches(turn):
                observed = turn['created_at']
                if isinstance(observed, str):
                    observed = datetime.fromisoformat(observed)
                return observed.astimezone(UTC).date() == yesterday and any(p.get('available') == 5 for p in turn['reply'].get('products', []))
            snapshots = [t for t in snapshots if matches(t)]
        selected = next((t for t in snapshots if t['turn_id'] == state.get('historical_turn_id')), None)
        if selected is None:
            if len(snapshots) != 1:
                raise ClarificationRequired("Précisez la réponse historique concernée : la référence est absente ou ambiguë dans cette conversation.")
            selected = snapshots[0]
        cards = selected['reply'].get('products', [])
        products = self._current_products(request, context, cards) if cards else []
        reference = {'historical_turn_id': selected['turn_id']}
        if kind == 'snapshot':
            if selected['reply'].get('intent') in {'RAG', 'RAG_CITATION'}:
                verified = self.documentary_reply(request, context, conversation_id, selected) if self.documentary_reply else None
                if not verified or verified.get('text') != selected['reply'].get('text'):
                    raise ClarificationRequired("La source historique n’est plus vérifiable ou accessible. Son contenu n’est pas reproduit.")
            old_text = selected['reply'].get('text', '')
            if len(old_text) > 7000:
                raise ClarificationRequired("Consultez cette réponse dans l’historique pour son contenu intégral.")
            return {'intent': 'HISTORY', 'text': "Réponse historique immuable (stocks et prix observés à cette date, sans valeur actuelle) :\n" + old_text,
                    'products': deepcopy(cards), **reference}
        if not products:
            raise ClarificationRequired("Cette réponse historique ne contient aucun produit identifiable à revérifier.")
        return {'intent': 'STOCK_CHECK', 'text': "Observation actuelle : prix et stocks revérifiés dans le catalogue autorisé. L’ancienne réponse reste inchangée ; aucune réservation effectuée.",
                'products': [self.catalogue.card(request, context, p) for p in products],
                'advice_skus': [p.sku for p in products], **reference}
