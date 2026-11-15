"""HSE's food-safety risk arithmetic (UAT 13), in one place.

Ratings 1 to 5. Severity × Probability × Detectability = the Risk Priority
Number; a Critical Control Point failure sets it to 46, whatever the three say.
"""
CCP_RPN = 46

PROBABILITY = {
    1: "Rare — once a year or less frequently",
    2: "Unlikely — occurs once every 6 months",
    3: "Possible — occurs monthly",
    4: "Likely — occurs weekly",
    5: "Certain — occurs daily",
}
SEVERITY = {
    1: "Insignificant — won’t cause injuries or illnesses",
    2: "Minor — can cause injuries or illnesses, only to a mild extent",
    3: "Significant — can cause injuries or illnesses that may require medical attention but limited treatment",
    4: "Major — can cause irreversible injuries or illnesses that require constant medical attention",
    5: "Severe — can result in fatality",
}
DETECTABILITY = {
    1: "Easy to detect without the use of equipment",
    2: "Detectable using rapid and/or simple equipment or test kits (under 1 hour)",
    3: "Detectable using moderately complex equipment in online testing labs (1 hour to 2 days)",
    4: "Detectable using complex equipment only available in accredited third-party labs (5 to 21 days)",
    5: "Undetectable",
}
# Highest first: the first band whose floor the RPN reaches.
BANDS = (
    ("critical", 45, "Critical", "Halt production — immediate action and correction required"),
    ("high", 25, "High", "Halt production — immediate CAPA required"),
    ("medium", 10, "Medium", "Mitigation within 30 days"),
    ("low", 0, "Low", "Monitor periodically or accept"),
)


def rpn(probability: int, severity: int, detectability: int, ccp_failure: bool) -> int:
    return CCP_RPN if ccp_failure else probability * severity * detectability


def band(value: int) -> str:
    for code, floor, _label, _action in BANDS:
        if value >= floor:
            return code
    return "low"
