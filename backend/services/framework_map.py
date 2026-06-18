"""
Control-framework mapping (crosswalk).

Auditors constantly need to show that one control satisfies several frameworks
at once. This engine holds a crosswalk of common ITGC control areas mapped
across SOX ITGC, COBIT 2019, ISO 27001:2022, NIST CSF 2.0 and SOC 2 (TSC).
Given a control area (or a free-text control description matched by keyword),
it returns the equivalent reference in every framework.
"""
from __future__ import annotations

# Each entry: a control area with its reference in each framework + keywords.
CROSSWALK = [
    {
        "area": "Logical Access / User Provisioning",
        "keywords": ["access", "provision", "user account", "joiner", "login", "authentication"],
        "sox": "ITGC - Access to Programs & Data",
        "cobit": "DSS05.04 Manage user identity & logical access",
        "iso": "A.5.15 / A.5.16 Access control & identity management",
        "nist": "PR.AA Identity Management & Access Control",
        "soc2": "CC6.1 Logical access controls",
    },
    {
        "area": "Segregation of Duties",
        "keywords": ["sod", "segregation", "duties", "conflict", "incompatible"],
        "sox": "ITGC - Access (SoD)",
        "cobit": "DSS06.03 Manage roles & segregation of duties",
        "iso": "A.5.3 Segregation of duties",
        "nist": "PR.AA-05 Access permissions with least privilege & SoD",
        "soc2": "CC6.3 Role-based access & SoD",
    },
    {
        "area": "Privileged Access Management",
        "keywords": ["privileged", "admin", "superuser", "root", "elevated"],
        "sox": "ITGC - Privileged Access",
        "cobit": "DSS05.04 Manage privileged access",
        "iso": "A.8.2 Privileged access rights",
        "nist": "PR.AA-05 Least privilege",
        "soc2": "CC6.1 / CC6.3 Privileged access",
    },
    {
        "area": "Access Recertification / Periodic Review",
        "keywords": ["recertification", "periodic review", "access review", "attestation", "ueba review"],
        "sox": "ITGC - Periodic Access Review",
        "cobit": "DSS05.04 Review access rights",
        "iso": "A.5.18 Review of access rights",
        "nist": "PR.AA-05 Review & update access",
        "soc2": "CC6.2 / CC6.3 Access review",
    },
    {
        "area": "Change Management",
        "keywords": ["change", "deployment", "release", "code", "migration", "promotion"],
        "sox": "ITGC - Change Management",
        "cobit": "BAI06 Manage IT changes",
        "iso": "A.8.32 Change management",
        "nist": "PR.PS-06 Secure development & change",
        "soc2": "CC8.1 Change management",
    },
    {
        "area": "Program Development (SDLC)",
        "keywords": ["sdlc", "development", "project", "implementation", "go-live"],
        "sox": "ITGC - Program Development",
        "cobit": "BAI03 Manage solutions build",
        "iso": "A.8.25 Secure development lifecycle",
        "nist": "PR.PS Platform security",
        "soc2": "CC8.1 SDLC",
    },
    {
        "area": "Backup & Recovery",
        "keywords": ["backup", "recovery", "restore", "resilience", "continuity"],
        "sox": "ITGC - Computer Operations",
        "cobit": "DSS04 Manage continuity / DSS01 Operations",
        "iso": "A.8.13 Information backup",
        "nist": "PR.DS / RC.RP Recovery planning",
        "soc2": "A1.2 Availability / backup",
    },
    {
        "area": "Job Scheduling & Monitoring",
        "keywords": ["job", "batch", "scheduling", "monitoring", "operations", "incident"],
        "sox": "ITGC - Computer Operations",
        "cobit": "DSS01 Manage operations",
        "iso": "A.8.16 Monitoring activities",
        "nist": "DE.CM Continuous monitoring",
        "soc2": "CC7.1 / CC7.2 Operations monitoring",
    },
]

FRAMEWORKS = [("sox", "SOX ITGC"), ("cobit", "COBIT 2019"),
              ("iso", "ISO 27001:2022"), ("nist", "NIST CSF 2.0"), ("soc2", "SOC 2 (TSC)")]


def all_mappings() -> list[dict]:
    return CROSSWALK


def search(query: str) -> list[dict]:
    """Match a free-text control description to crosswalk entries by keyword."""
    q = query.strip().lower()
    if not q:
        return []
    scored = []
    for entry in CROSSWALK:
        score = 0
        if q in entry["area"].lower():
            score += 5
        for kw in entry["keywords"]:
            if kw in q or q in kw:
                score += 2
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored]
