"""Flag state from the MMSI Maritime Identification Digits (ITU MID), keyless.

Covers the flags that matter around French Guiana plus major fishing and open
registries. French overseas territories have their own MIDs (745 = French
Guiana): all of them count as French for the EEZ rules.
"""

from __future__ import annotations

from typing import Optional

MID_COUNTRIES = {
    # France and overseas
    "226": "France", "227": "France", "228": "France", "329": "Guadeloupe (FR)", "347": "Martinique (FR)",
    "361": "Saint-Pierre-et-Miquelon (FR)", "501": "Terres australes (FR)", "540": "Nouvelle-Calédonie (FR)",
    "546": "Polynésie française (FR)", "578": "Wallis-et-Futuna (FR)", "607": "Saint-Paul et Amsterdam (FR)",
    "618": "Crozet (FR)", "635": "Kerguelen (FR)", "660": "La Réunion (FR)", "745": "Guyane (FR)",
    # Neighbours and the Caribbean / South America
    "710": "Brésil", "765": "Suriname", "750": "Guyana", "775": "Venezuela", "362": "Trinité-et-Tobago",
    "314": "Barbade", "325": "Jamaïque", "330": "Grenade", "375": "Saint-Vincent", "376": "Saint-Vincent",
    "377": "Saint-Vincent", "341": "Saint-Kitts", "343": "Sainte-Lucie", "306": "Curaçao / Antilles NL",
    "307": "Aruba", "730": "Colombie", "735": "Équateur", "760": "Pérou", "725": "Chili", "701": "Argentine",
    "770": "Uruguay", "755": "Paraguay", "720": "Bolivie",
    # Distant-water fishing fleets
    "412": "Chine", "413": "Chine", "414": "Chine", "416": "Taïwan", "440": "Corée du Sud", "441": "Corée du Sud",
    "431": "Japon", "432": "Japon", "273": "Russie", "224": "Espagne", "225": "Espagne", "255": "Portugal (Madère)",
    "263": "Portugal", "247": "Italie", "237": "Grèce", "239": "Grèce", "240": "Grèce", "241": "Grèce",
    "244": "Pays-Bas", "245": "Pays-Bas", "246": "Pays-Bas", "257": "Norvège", "258": "Norvège", "259": "Norvège",
    "232": "Royaume-Uni", "233": "Royaume-Uni", "234": "Royaume-Uni", "235": "Royaume-Uni", "211": "Allemagne",
    "218": "Allemagne", "219": "Danemark", "220": "Danemark", "230": "Finlande", "265": "Suède", "266": "Suède",
    "663": "Sénégal", "627": "Ghana", "657": "Nigeria", "613": "Cameroun", "242": "Maroc", "605": "Algérie",
    "548": "Philippines", "525": "Indonésie", "574": "Viêt Nam", "567": "Thaïlande", "533": "Malaisie",
    "419": "Inde", "463": "Pakistan", "405": "Bangladesh",
    "338": "États-Unis", "366": "États-Unis", "367": "États-Unis", "368": "États-Unis", "369": "États-Unis",
    "316": "Canada", "345": "Mexique",
    # Open registries / flags of convenience
    "351": "Panama", "352": "Panama", "353": "Panama", "354": "Panama", "355": "Panama", "356": "Panama",
    "357": "Panama", "370": "Panama", "371": "Panama", "372": "Panama", "373": "Panama", "374": "Panama",
    "636": "Libéria", "637": "Libéria", "538": "Îles Marshall", "215": "Malte", "229": "Malte", "248": "Malte",
    "249": "Malte", "256": "Malte", "308": "Bahamas", "309": "Bahamas", "311": "Bahamas", "312": "Belize",
    "334": "Honduras", "209": "Chypre", "210": "Chypre", "212": "Chypre", "518": "Îles Cook", "667": "Sierra Leone",
    "671": "Togo", "620": "Comores", "572": "Tuvalu", "576": "Vanuatu", "577": "Vanuatu", "511": "Palaos",
    "631": "Guinée équatoriale",
}


def flag_from_mmsi(mmsi: object) -> Optional[str]:
    """Country of a ship station MMSI (9 digits starting with 2-7), else None."""
    text = str(mmsi or "").strip()
    if len(text) != 9 or not text.isdigit() or text[0] not in "234567":
        return None
    return MID_COUNTRIES.get(text[:3])


def is_french(flag: Optional[str]) -> bool:
    return bool(flag) and (flag == "France" or flag.endswith("(FR)"))


# ISO 3166-1 alpha-3 flags (Global Fishing Watch, VMS FS field) -> display name.
# French overseas territories have their own ISO codes: they are French flags.
FRENCH_ISO3 = {"FRA", "GUF", "MTQ", "GLP", "REU", "MYT", "SPM", "BLM", "MAF", "NCL", "PYF", "WLF", "ATF"}
ISO3_NAMES = {
    "FRA": "France", "GUF": "Guyane (FR)", "MTQ": "Martinique (FR)", "GLP": "Guadeloupe (FR)", "REU": "La Réunion (FR)",
    "MYT": "Mayotte (FR)", "SPM": "Saint-Pierre-et-Miquelon (FR)", "BLM": "Saint-Barthélemy (FR)", "MAF": "Saint-Martin (FR)",
    "NCL": "Nouvelle-Calédonie (FR)", "PYF": "Polynésie française (FR)", "WLF": "Wallis-et-Futuna (FR)",
    "ATF": "Terres australes (FR)",
    "BRA": "Brésil", "SUR": "Suriname", "GUY": "Guyana", "VEN": "Venezuela", "TTO": "Trinité-et-Tobago", "BRB": "Barbade",
    "COL": "Colombie", "ECU": "Équateur", "PER": "Pérou", "CHL": "Chili", "ARG": "Argentine", "URY": "Uruguay",
    "CHN": "Chine", "TWN": "Taïwan", "KOR": "Corée du Sud", "JPN": "Japon", "RUS": "Russie", "ESP": "Espagne",
    "PRT": "Portugal", "ITA": "Italie", "GRC": "Grèce", "NLD": "Pays-Bas", "NOR": "Norvège", "GBR": "Royaume-Uni",
    "DEU": "Allemagne", "DNK": "Danemark", "USA": "États-Unis", "CAN": "Canada", "MEX": "Mexique", "SEN": "Sénégal",
    "GHA": "Ghana", "NGA": "Nigeria", "MAR": "Maroc", "PHL": "Philippines", "IDN": "Indonésie", "VNM": "Viêt Nam",
    "THA": "Thaïlande", "IND": "Inde", "PAN": "Panama", "LBR": "Libéria", "MHL": "Îles Marshall", "MLT": "Malte",
    "BHS": "Bahamas", "BLZ": "Belize", "HND": "Honduras", "CYP": "Chypre", "COK": "Îles Cook", "SLE": "Sierra Leone",
    "TGO": "Togo", "COM": "Comores", "TUV": "Tuvalu", "VUT": "Vanuatu", "PLW": "Palaos", "GNQ": "Guinée équatoriale",
    "JAM": "Jamaïque", "GRD": "Grenade", "VCT": "Saint-Vincent", "KNA": "Saint-Kitts", "LCA": "Sainte-Lucie",
    "CUW": "Curaçao", "ABW": "Aruba",
}


def flag_name(code: Optional[str]) -> Optional[str]:
    """Display name of a flag given as ISO3 code (``VEN`` -> ``Venezuela``); names pass through."""
    if not code:
        return None
    text = str(code).strip()
    return ISO3_NAMES.get(text.upper(), text) if len(text) == 3 else text


def is_french_flag(value: Optional[str]) -> bool:
    """French flag, given either as a display name or an ISO3 code (overseas included)."""
    if not value:
        return False
    text = str(value).strip()
    return text.upper() in FRENCH_ISO3 or is_french(text)
