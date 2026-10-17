"""Deterministic French copy from verified Advice facts, without a currency claim."""
from dataclasses import replace
from decimal import Decimal

from stockpilot.application.dto.advice_criteria import AdviceCriteria
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory


_CATEGORY_NAMES = {
    ProductCategory.DRILL: ('perceuse', 'perceuses'),
    ProductCategory.SCREWDRIVER: ('visseuse', 'visseuses'),
    ProductCategory.SAW: ('scie', 'scies'),
    ProductCategory.SANDER: ('ponceuse', 'ponceuses'),
}


def catalogue_number(value: float | Decimal, *, price: bool = False) -> str:
    """Preserve catalogue precision; French separators do not imply a currency."""
    number = Decimal(str(value))
    raw = format(number if number else Decimal(0), 'f')
    whole, _, decimals = raw.partition('.')
    decimals = decimals.rstrip('0')
    if price:
        decimals = decimals.ljust(2, '0')
    # Group digits without reversing their order, including an optional sign.
    sign = '-' if whole.startswith('-') else ''
    digits = whole.lstrip('-')
    groups = [digits[max(0, end - 3):end] for end in range(len(digits), 0, -3)]
    grouped = sign + '\u202f'.join(reversed(groups))
    return grouped + (',' + decimals if decimals else '')


def _noun(criteria: AdviceCriteria, *, singular: bool = False) -> str:
    names = _CATEGORY_NAMES.get(criteria.category, ('produit', 'produits'))
    return names[0 if singular else 1]


def _search_description(criteria: AdviceCriteria, *, singular: bool = False) -> str:
    description = _noun(criteria, singular=singular)
    if criteria.available is not None:
        description += (' disponible' if criteria.available else ' indisponible') + ('' if singular else 's')
    if criteria.minimum is not None:
        description += f' à partir de {catalogue_number(criteria.minimum)}'
    if criteria.maximum is not None:
        description += ' et' if criteria.minimum is not None else ''
        description += (f' à moins de {catalogue_number(criteria.maximum)}' if criteria.exclusive_maximum
                        else f' au prix maximum de {catalogue_number(criteria.maximum)}')
    return description


def no_results_summary(criteria: AdviceCriteria) -> str:
    return (f"Je n’ai trouvé aucun produit correspondant à votre recherche de {_search_description(criteria)}. "
            'Vous pouvez modifier votre budget ou vos critères.')


def advice_summary(criteria: AdviceCriteria, products: list[Product]) -> str:
    count = len(products)
    if criteria.mode == 'product':
        product = products[0]
        availability = (f'Ce produit est disponible, avec {product.stock} '
                        f'{"unité" if product.stock == 1 else "unités"} en stock au moment de la recherche.'
                        if product.available else 'Ce produit est indisponible au moment de la recherche : aucune unité en stock.')
        summary = f'Voici la fiche de {product.name}. {availability}'
        if not criteria.matches(product):
            summary += ' Cette fiche ne respecte pas tous vos critères ; ce produit n’est pas une correspondance valide.'
        return summary
    if criteria.mode == 'compare':
        alternatives = count - 1
        summary = ('Voici les différences de prix et de disponibilité entre le produit de référence et '
                   f'{alternatives} {"alternative" if alternatives == 1 else "alternatives"}.'
                   if alternatives else 'Voici le produit de référence. Aucune alternative ne correspond à vos critères.')
        if not replace(criteria, exclude_skus=()).matches(products[0]):
            summary += ' Le produit de référence ne respecte pas tous vos critères.'
            if criteria.maximum is not None and (products[0].price >= criteria.maximum if criteria.exclusive_maximum
                                                 else products[0].price > criteria.maximum):
                summary += ' Il est hors budget.'
        return summary + ' Les caractéristiques techniques ne sont pas renseignées dans le catalogue.'
    if criteria.mode == 'cheapest':
        product = products[0]
        return (f'Parmi les produits précédemment présentés et encore vérifiables, {product.name} '
                f'a le prix le plus bas : {catalogue_number(product.price, price=True)}.')
    return (f'J’ai trouvé {count} {_search_description(criteria, singular=count == 1)}. '
            + ('Voici le produit correspondant.' if count == 1 else 'Les résultats sont classés par prix croissant.'))


def relevance_reasons(criteria: AdviceCriteria, product: Product) -> dict[str, str]:
    """Explain applicable constraints instead of repeating every card field."""
    return {
        'categorie': ('Correspond à la catégorie recherchée.'
                      if criteria.category is not None and product.category == criteria.category else ''),
        'prix': (f'Respecte le plafond {"strict" if criteria.exclusive_maximum else "inclusif"} '
                 f'de {catalogue_number(criteria.maximum)}.'
                 if criteria.maximum is not None and (product.price < criteria.maximum if criteria.exclusive_maximum
                                                       else product.price <= criteria.maximum) else ''),
        'stock': ('Disponibilité conforme à votre recherche.'
                  if criteria.available is not None and product.available == criteria.available else ''),
    }
