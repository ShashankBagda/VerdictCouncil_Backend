"""Gold-set eval fixtures for pipeline validation.

Three test cases covering both domains. Each returns a CaseState-compatible
dict with raw_documents populated with representative sample text.
"""

REFUND_DISPUTE = {
    "case_id": "eval-refund-001",
    "run_id": "eval-run-001",
    "domain": "small_claims",
    "status": "pending",
    "parties": [
        {"name": "Amelia Tan", "role": "claimant"},
        {"name": "Horizon Electronics Pte Ltd", "role": "respondent"},
    ],
    "case_metadata": {
        "description": "Late delivery refund dispute. Claimant ordered a laptop on 15 Nov 2025 "
        "with guaranteed 5-day delivery. Item arrived 22 days late with missing accessories. "
        "Claimant seeks full refund of $850. Respondent claims delivery was within extended "
        "holiday period terms.",
        "claim_amount": 850,
        "dispute_type": "refund_dispute",
    },
    "raw_documents": [
        {
            "filename": "Applicant_Position_Statement.pdf",
            "content": (
                "I, Amelia Tan, purchased a Horizon ProBook X15 laptop from Horizon Electronics "
                "on 15 November 2025 for $850. The listing stated '5-day guaranteed delivery'. "
                "The laptop arrived on 7 December 2025, 22 days late, and was missing the charger "
                "and carrying case listed in the product description. I contacted support on "
                "8 December and was told accessories were 'out of stock'. I request a full refund "
                "as the product was not delivered as described within the guaranteed timeframe."
            ),
        },
        {
            "filename": "Merchant_Response.pdf",
            "content": (
                "Horizon Electronics acknowledges the delayed delivery. Our terms and conditions, "
                "updated on 1 November 2025, include a clause extending delivery guarantees during "
                "the holiday period (15 Nov - 31 Dec). The customer was notified of this extension "
                "via email on 16 November 2025. The missing accessories are being "
                "shipped separately "
                "and were dispatched on 10 December 2025. We offer a 15% discount on the next "
                "purchase as goodwill but deny refund eligibility."
            ),
        },
        {
            "filename": "Invoice_2025-11-15.pdf",
            "content": (
                "Invoice #HE-2025-11823\n"
                "Date: 15 November 2025\n"
                "Item: Horizon ProBook X15 Laptop Bundle\n"
                "  - Laptop: $750\n"
                "  - Charger: $50\n"
                "  - Carrying Case: $50\n"
                "Total: $850\n"
                "Delivery: 5-day guaranteed\n"
                "Payment: Visa ending 4829"
            ),
        },
    ],
}

SERVICE_COMPLAINT = {
    "case_id": "eval-service-001",
    "run_id": "eval-run-002",
    "domain": "small_claims",
    "status": "pending",
    "parties": [
        {"name": "Daniel Lim", "role": "claimant"},
        {"name": "Northline Renovations", "role": "respondent"},
    ],
    "case_metadata": {
        "description": "Home renovation service complaint. Claimant contracted respondent for "
        "kitchen renovation at $4,200. Work stopped at 60% completion after 3 months with "
        "multiple defects. Respondent claims delays caused by claimant's scope changes.",
        "claim_amount": 4200,
        "dispute_type": "service_complaint",
    },
    "raw_documents": [
        {
            "filename": "Owner_Complaint.pdf",
            "content": (
                "I contracted Northline Renovations on 1 August 2025 for a kitchen renovation "
                "at $4,200. The contract specified completion within 6 weeks. After 3 months, "
                "the work is approximately 60% complete. The installed countertop has visible "
                "cracks, cabinet doors are misaligned, and the backsplash tiles are uneven. "
                "I have paid $2,520 (60% of contract value) in progress payments. The contractor "
                "has not appeared on site since 15 October 2025. I seek the remaining work to "
                "be completed or a refund of $1,680 to hire another contractor."
            ),
        },
        {
            "filename": "Contractor_Response.pdf",
            "content": (
                "Northline Renovations confirms the project timeline was extended due to three "
                "scope change requests from Mr. Lim: (1) upgrade from laminate to "
                "quartz countertop (agreed 20 Aug), (2) addition of under-cabinet lighting "
                "(agreed 5 Sep), (3) change of backsplash material from ceramic to mosaic "
                "(agreed 18 Sep). Each change required "
                "remeasurement and reordering. The tile supplier experienced a 3-week delay. We "
                "dispute the defect claims — the countertop was inspected at installation and "
                "the crack appeared after Mr. Lim placed heavy equipment on it."
            ),
        },
    ],
}

TRAFFIC_APPEAL = {
    "case_id": "eval-traffic-001",
    "run_id": "eval-run-003",
    "domain": "traffic_violation",
    "status": "pending",
    "parties": [
        {"name": "Siti Rahman", "role": "accused"},
        {"name": "Land Transport Authority", "role": "prosecution"},
    ],
    "case_metadata": {
        "description": "Traffic camera appeal for improper lane change. Driver contests the "
        "violation citing obscured lane markings due to roadworks and confusing temporary signage.",
        "offence_code": "RTA-S65",
        "fine_amount": 300,
    },
    "raw_documents": [
        {
            "filename": "Driver_Appeal_Statement.pdf",
            "content": (
                "On 3 October 2025 at approximately 14:30, I was driving along Tampines Ave 4 "
                "when I encountered roadworks that had obscured the lane markings. Temporary "
                "cones were placed inconsistently and the lane guide signs were partially blocked "
                "by construction equipment. I made what I believed was a legal lane change based "
                "on the visible temporary markings. The traffic camera captured this "
                "as an improper "
                "lane change. I have dashcam footage showing the obscured markings and confusing "
                "temporary signage."
            ),
        },
        {
            "filename": "Agency_Enforcement_Summary.pdf",
            "content": (
                "Camera ID: TC-TA4-0892 captured vehicle SJK 4521E executing an improper lane "
                "change at Tampines Ave 4, Junction 12 on 3 Oct 2025 at 14:32:15. The vehicle "
                "crossed a solid white line to change from Lane 2 to Lane 1. While roadworks "
                "were present in the area, the solid white line at the junction was not obscured "
                "by construction activity. The camera system was calibrated on 1 Oct 2025 and "
                "was functioning within specifications. Fine: $300 under Road Traffic Act S.65."
            ),
        },
    ],
}

ALL_FIXTURES = [REFUND_DISPUTE, SERVICE_COMPLAINT, TRAFFIC_APPEAL]
