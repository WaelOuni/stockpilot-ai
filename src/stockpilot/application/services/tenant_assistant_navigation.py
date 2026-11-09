"""Bounded textual navigation over an authorized, persisted catalogue query.

No cached product list or model output is used to answer. Every operation runs
the filters in the catalogue store again; old turn snapshots remain immutable.
"""

import re
from dataclasses import asdict, replace
from datetime import UTC, datetime

from stockpilot.application.use_cases.catalogue_advice import ClarificationRequired, normalize
from stockpilot.application.use_cases.resolve_product import explicit_skus
from stockpilot.domain.entities.tenant_product import TenantProductQuery


def is_navigation(question):
    value = normalize(question).strip()
    if re.search(r'["«»]', question):
        return False
    return bool(re.match(
        r"(?:(?:passe|retourne|montre|affiche|je veux)\b.*\bpage\b|page\b|"
        r"(?:affiche les |montre les \d+ produits )?suivants?\b|precedents?\b|"
        r"voir plus de produits\b|trie les (?:resultats|produits)\b|"
        r"exclus les produits en rupture de cette liste\b|ne montre que ceux avec un stock\b|"
        r"(?:le sku|cherche)\b.*\bdans les (?:\d+ )?resultats\b|affiche tous les\b)", value
    ) or re.search(r'[?!.]\s*affiche les suivants[.!? ]*$', value))


def _query(saved):
    if not isinstance(saved, dict):
        raise ClarificationRequired("Commencez par une recherche dans cette conversation.")
    values = dict(saved)
    for field in ('categories', 'exclude_skus'):
        values[field] = tuple(values.get(field, ()))
    return TenantProductQuery(**values)


class TenantAssistantNavigation:
    def __init__(self, catalogue):
        self.catalogue = catalogue

    def execute(self, request, context, question, saved):
        value = normalize(question).strip().rstrip('.?! ')
        # One explicit search followed by navigation is parsed as two clauses,
        # never by removing arbitrary qualifiers from the original request.
        compound = re.fullmatch(r'(.+?)\s*[?!.]\s*affiche les suivants', value)
        seed_context = {}
        if compound:
            seed = self.catalogue.low_stock(request, context, compound[1])
            saved = seed['catalogue_search']
            seed_context = {key: seed[key] for key in ('criteria', 'catalogue_categories')}
            value = 'affiche les suivants'
        query = _query(saved)
        update = {}
        last_page = False
        membership = False
        if re.fullmatch(r'(?:affiche les suivants|voir plus de produits|montre la page suivante|page suivante|suivant|suivants)', value):
            update['offset'] = query.offset + query.limit
        elif re.fullmatch(r'(?:montre la page precedente|page precedente|precedent|precedents)', value):
            update['offset'] = max(0, query.offset - query.limit)
        elif count := re.fullmatch(r'montre les (\d+) produits suivants', value):
            update.update(offset=query.offset + query.limit, limit=int(count[1]))
        elif page := re.fullmatch(r'(?:passe a la|je veux la|montre la|affiche la) page (\d+)(?: des resultats)?', value):
            if int(page[1]) < 1:
                raise ClarificationRequired("La première page porte le numéro 1.")
            update['offset'] = (int(page[1]) - 1) * query.limit
        elif value in ('retourne a la premiere page', 'premiere page'):
            update['offset'] = 0
        elif value == 'affiche la derniere page des resultats':
            last_page = True
        elif sorting := re.fullmatch(r'trie les (?:resultats|produits) par (stock disponible croissant|prix croissant|sku)', value):
            update.update(offset=0, sort_by_price=sorting[1] == 'prix croissant', sort_by_available=sorting[1] == 'stock disponible croissant')
        elif value == 'exclus les produits en rupture de cette liste':
            update.update(offset=0, available=True)
        elif bounds := re.fullmatch(r'ne montre que ceux avec un stock de (\d+) a (\d+) unites', value):
            update.update(offset=0, minimum_available=int(bounds[1]), maximum_available=int(bounds[2]))
        elif re.fullmatch(r'(?:le sku .+? est-il|cherche (?:le sku )?.+?) dans les (?:\d+ )?resultats', value):
            literal = re.fullmatch(r'(?:le sku (\S+) est-il|cherche (?:le sku )?(\S+)) dans les (?:\d+ )?r[ée]sultats[.?! ]*', question.strip(), re.I)
            sku = next((v for v in literal.groups() if v), None) if literal else None
            if not sku or sku not in explicit_skus(sku):
                raise ClarificationRequired("Indiquez le SKU exact à rechercher dans les résultats ; le nombre annoncé n’est pas une preuve de correspondance.")
            update.update(offset=0, limit=1, exact_sku=sku)
            membership = True
        else:
            raise ClarificationRequired("Choisissez une page, suivant/précédent, ou un tri pris en charge. Une page contient de 1 à 100 résultats, jamais tous les résultats d’un coup.")
        try:
            current = replace(query, **update, status='active')
        except ValueError:
            raise ClarificationRequired("Pagination ou plage de stock invalide : 1 à 100 résultats par page, décalage maximal 10000, bornes de stock entières et ordonnées.") from None
        if last_page:
            count = self.catalogue.catalogue.search_stock(request, context, replace(current, offset=0, limit=1)).total
            offset = max(0, (count - 1) // current.limit * current.limit)
            if offset > 10000:
                raise ClarificationRequired("La dernière page dépasse la borne de navigation ; affinez les filtres.")
            current = replace(current, offset=offset)
        page = self.catalogue.catalogue.search_stock(request, context, current)
        observed = datetime.now(UTC).isoformat()
        cards = [self.catalogue.card(request, context, pair.product, pair.stock, observed) for pair in page.items[:5]]
        if membership:
            summary = 'SKU présent dans les résultats actuels.' if page.total else 'Aucune correspondance pour ce SKU dans les résultats actuels.'
        else:
            summary = f'{page.total} résultat(s) actuel(s) ; décalage {current.offset}, page bornée à {current.limit}, {len(cards)} fiche(s) affichée(s).'
        return {
            'intent': 'LOW_STOCK' if current.sort_by_available else 'PRODUCT_ADVICE',
            'text': summary + ' Filtres revalidés ; les stocks peuvent changer entre les pages. Aucun stock réservé.',
            'products': cards, 'total': page.total,
            **seed_context,
            'advice_skus': [card['sku'] for card in cards],
            # Membership is a question about the saved set, not a replacement
            # of that set by a single product for subsequent navigation.
            'catalogue_search': asdict(query if membership else current),
        }
