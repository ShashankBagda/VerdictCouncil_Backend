"""
One-shot demo data fixer.

Fixes:
  1. Cases with status='decided' (invalid enum) → causes 500 → CORS failure
  2. Seeds the two default domains (small_claims, traffic_violation)
  3. Upserts two rich demo cases (one per domain) with full pipeline output

Run:
    python scripts/fix_demo_data.py
"""
import os
import sys
import uuid
from datetime import UTC, date, datetime, time

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

from src.models.case import (
    Argument,
    ArgumentSide,
    Case,
    CaseComplexity,
    CaseDomain,
    CaseRoute,
    CaseStatus,
    Document,
    Evidence,
    EvidenceStrength,
    EvidenceType,
    Fact,
    FactConfidence,
    FactStatus,
    HearingAnalysis,
    LegalRule,
    Party,
    PartyRole,
    Precedent,
    PrecedentSource,
    Witness,
)
from src.models.domain import Domain
from src.models.user import User

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://vc_dev:vc_dev_password@localhost:5432/verdictcouncil"
)

ADMIN_ID = uuid.UUID("00000000-0000-4000-a000-000000000002")
JUDGE_ID = uuid.UUID("00000000-0000-4000-a000-000000000001")

DOMAIN_SC_ID = uuid.UUID("d0000000-0000-4000-a000-000000000001")
DOMAIN_TV_ID = uuid.UUID("d0000000-0000-4000-a000-000000000002")

CASE_TV_ID = uuid.UUID("20000000-0000-4000-a000-000000000001")
CASE_SC_ID = uuid.UUID("20000000-0000-4000-a000-000000000002")


def main() -> None:
    engine = create_engine(DATABASE_URL)

    # ── 1. Fix 'decided' status ──────────────────────────────────────────────
    with engine.connect() as conn:
        result = conn.execute(
            text("UPDATE cases SET status='closed' WHERE status='decided' RETURNING id")
        )
        fixed = result.fetchall()
        conn.commit()
    if fixed:
        print(f"[fix] Updated {len(fixed)} case(s) from 'decided' → 'closed': {[str(r[0]) for r in fixed]}")
    else:
        print("[fix] No 'decided' status cases found.")

    with Session(engine) as session:
        # ── 2. Seed default domains ──────────────────────────────────────────
        from sqlalchemy import select

        domain_id_map = {}  # code -> UUID actually in DB
        for domain_id, code, name, desc in [
            (
                DOMAIN_SC_ID,
                "small_claims",
                "Small Claims Tribunal",
                "Handles monetary disputes up to SGD 20,000 (or SGD 30,000 with consent) "
                "under the Small Claims Tribunals Act (Cap. 308C). Covers sales of goods, "
                "provision of services, tenancy disputes, and property damage claims.",
            ),
            (
                DOMAIN_TV_ID,
                "traffic_violation",
                "Traffic Violation Court",
                "Processes road traffic offence cases under the Road Traffic Act (Cap. 276) "
                "and Road Traffic Rules. Covers speeding, red-light running, drink driving, "
                "reckless driving, and related offences.",
            ),
        ]:
            # Look up by code (unique), not by our UUID which may not match DB
            row = session.execute(
                select(Domain).where(Domain.code == code)
            ).scalar_one_or_none()
            if row:
                domain_id_map[code] = row.id
                print(f"[domain] '{code}' already exists (id={row.id}) — skipping.")
            else:
                d = Domain(
                    id=domain_id,
                    code=code,
                    name=name,
                    description=desc,
                    is_active=True,
                    created_by=ADMIN_ID,
                )
                session.add(d)
                session.flush()
                domain_id_map[code] = d.id
                print(f"[domain] Inserted: {name} (id={d.id})")

        DOMAIN_SC_ID_ACTUAL = domain_id_map["small_claims"]
        DOMAIN_TV_ID_ACTUAL = domain_id_map["traffic_violation"]

        # ── 3. Demo case: Traffic Violation ──────────────────────────────────
        if session.get(Case, CASE_TV_ID):
            print(f"[case] Traffic demo case already exists — skipping.")
        else:
            case_tv = Case(
                id=CASE_TV_ID,
                domain=CaseDomain.traffic_violation,
                domain_id=DOMAIN_TV_ID_ACTUAL,
                title="PP v Ahmad bin Ismail — Speeding on PIE (S63(1) RTA)",
                description=(
                    "Accused was detected travelling at 132 km/h in a 90 km/h zone on the "
                    "Pan-Island Expressway at 02:15 on 14 January 2026 by a fixed speed camera "
                    "(Camera ID PIE-KM23-A). The accused did not contest the speed reading but "
                    "submits that his speedometer was defective and he was unaware of his speed."
                ),
                filed_date=date(2026, 2, 3),
                offence_code="RTA_S63_1",
                status=CaseStatus.ready_for_review,
                jurisdiction_valid=True,
                complexity=CaseComplexity.low,
                route=CaseRoute.proceed_automated,
                created_by=JUDGE_ID,
                judicial_decision={
                    "verdict": "guilty",
                    "reasoning": (
                        "The prosecution has proved beyond reasonable doubt that the accused "
                        "exceeded the speed limit by 47%. Speed camera evidence is certified "
                        "and uncontested. The defence of defective speedometer is not supported "
                        "by any servicing records or independent inspection report, and does not "
                        "negate the offence under s 63(1) RTA which is one of strict liability."
                    ),
                    "sentence": {
                        "fine_sgd": 800,
                        "disqualification_weeks": 0,
                        "demerit_points": 8,
                    },
                    "mitigating_factors": ["First offender", "Guilty plea tendered"],
                    "aggravating_factors": [
                        "47% over the posted limit",
                        "Early morning — reduced visibility conditions",
                    ],
                    "confidence": 92,
                },
            )
            session.add(case_tv)
            session.flush()

            # Parties
            party_prose = Party(case_id=CASE_TV_ID, name="State Prosecutor", role=PartyRole.prosecution)
            party_accused = Party(
                case_id=CASE_TV_ID, name="Ahmad bin Ismail", role=PartyRole.accused,
                contact_info={"nric": "S8812345A", "address": "Blk 45 Ang Mo Kio Ave 3 #08-12"}
            )
            session.add_all([party_prose, party_accused])
            session.flush()

            # Document
            doc_tv = Document(
                case_id=CASE_TV_ID,
                filename="speed_camera_report_PIE_KM23A_20260114.pdf",
                file_type="application/pdf",
                uploaded_by=ADMIN_ID,
                uploaded_at=datetime(2026, 2, 3, 10, 0, tzinfo=UTC),
            )
            session.add(doc_tv)
            session.flush()

            # Evidence
            session.add_all([
                Evidence(
                    case_id=CASE_TV_ID,
                    document_id=doc_tv.id,
                    evidence_type=EvidenceType.documentary,
                    strength=EvidenceStrength.strong,
                    admissibility_flags={
                        "certified": True,
                        "calibration_valid": True,
                        "chain_of_custody_intact": True,
                    },
                    linked_claims={"charge": "speeding_132kmh_in_90kmh_zone"},
                ),
                Evidence(
                    case_id=CASE_TV_ID,
                    document_id=None,
                    evidence_type=EvidenceType.testimonial,
                    strength=EvidenceStrength.weak,
                    admissibility_flags={"authenticated": False},
                    linked_claims={"defence": "defective_speedometer_claim"},
                ),
            ])

            # Facts
            session.add_all([
                Fact(
                    case_id=CASE_TV_ID,
                    event_date=date(2026, 1, 14),
                    event_time=time(2, 15),
                    description="Accused's vehicle (SFQ1234Z) recorded at 132 km/h by camera PIE-KM23-A at KM 23.1 on PIE. Speed limit at that stretch is 90 km/h.",
                    confidence=FactConfidence.high,
                    status=FactStatus.agreed,
                    source_document_id=doc_tv.id,
                ),
                Fact(
                    case_id=CASE_TV_ID,
                    event_date=date(2026, 1, 14),
                    description="Camera PIE-KM23-A was last calibrated on 5 November 2025 by LTA-accredited engineer, within validity period.",
                    confidence=FactConfidence.high,
                    status=FactStatus.agreed,
                    source_document_id=doc_tv.id,
                ),
                Fact(
                    case_id=CASE_TV_ID,
                    description="Accused claims his vehicle's speedometer was defective but has not produced any workshop inspection report or servicing records to support this.",
                    confidence=FactConfidence.low,
                    status=FactStatus.disputed,
                ),
            ])

            # Witness
            session.add(Witness(
                case_id=CASE_TV_ID,
                name="Sgt Muhammad Fadzillah bin Rashid",
                role="LTA Traffic Enforcement Officer",
                party_id=party_prose.id,
                credibility_score=90,
                bias_indicators={"institutional_affiliation": "LTA"},
                simulated_testimony=(
                    "I am a Traffic Enforcement Officer with LTA. On 14 January 2026 I reviewed "
                    "the image and data extracted from Camera PIE-KM23-A. The camera recorded "
                    "vehicle SFQ1234Z travelling at 132 km/h. The camera was within its calibration "
                    "validity period. I confirm the accuracy of the speed reading to the best of my knowledge."
                ),
            ))

            # Legal rules
            session.add_all([
                LegalRule(
                    case_id=CASE_TV_ID,
                    statute_name="Road Traffic Act",
                    section="s 63(1)",
                    verbatim_text=(
                        "Except as otherwise provided by this Act or any rules made thereunder, "
                        "any person who drives a motor vehicle on a road at a speed exceeding "
                        "the speed limit applicable to that road shall be guilty of an offence."
                    ),
                    relevance_score=1.0,
                    application=(
                        "Strict liability offence. Prosecution need only prove the speed reading "
                        "exceeded the posted limit. A defective speedometer is not a defence."
                    ),
                ),
                LegalRule(
                    case_id=CASE_TV_ID,
                    statute_name="Road Traffic (Enforcement) Rules",
                    section="r 4(2)",
                    verbatim_text="A speed-measuring device approved by the Authority shall be presumed to be accurate unless the contrary is proved.",
                    relevance_score=0.85,
                    application="The speed camera data is presumed accurate; no contrary evidence was adduced by the accused.",
                ),
            ])

            # Precedents
            session.add_all([
                Precedent(
                    case_id=CASE_TV_ID,
                    citation="PP v Lim Ah Kow [2019] SGMC 12",
                    court="Magistrate's Court, Singapore",
                    outcome="Convicted",
                    reasoning_summary=(
                        "Speed camera evidence sufficient; accused's bare denial insufficient "
                        "to rebut presumption of accuracy."
                    ),
                    similarity_score=0.88,
                    source=PrecedentSource.curated,
                ),
            ])

            # Arguments
            session.add_all([
                Argument(
                    case_id=CASE_TV_ID,
                    side=ArgumentSide.prosecution,
                    legal_basis=(
                        "Under s 63(1) RTA, the offence is strict liability. The certified speed "
                        "camera reading of 132 km/h in a 90 km/h zone is sufficient proof. "
                        "The camera was calibrated within validity. The accused's own vehicle was "
                        "identified by its registration plate."
                    ),
                    supporting_evidence={"documents": ["speed_camera_report_PIE_KM23A_20260114.pdf"]},
                    weaknesses="None significant — calibration certificate is current.",
                    suggested_questions={"to_accused": [
                        "Can you produce any workshop records showing a speedometer fault?",
                        "When was the last time you had your vehicle serviced?",
                    ]},
                ),
                Argument(
                    case_id=CASE_TV_ID,
                    side=ArgumentSide.defense,
                    legal_basis=(
                        "The accused did not intentionally speed. His speedometer was defective "
                        "and he had no means of knowing his true speed. A reasonable driver would "
                        "rely on his vehicle's instrumentation."
                    ),
                    supporting_evidence={},
                    weaknesses=(
                        "No documentary evidence of speedometer fault. s 63(1) RTA is strict "
                        "liability — absence of intent is not a defence. Accused failed to adduce "
                        "any independent inspection report."
                    ),
                    suggested_questions={"to_prosecution": [
                        "Is the camera's calibration record available for inspection?",
                    ]},
                ),
            ])

            # Hearing analysis
            session.add(HearingAnalysis(
                case_id=CASE_TV_ID,
                reasoning_chain={
                    "step1_intake": "Case classified as traffic_violation. Jurisdiction valid. Offence code RTA_S63_1.",
                    "step2_evidence": "Speed camera report admitted. Defence witness (accused's bare claim) has low probative value.",
                    "step3_facts": "Speed of 132 km/h in 90 km/h zone established beyond reasonable doubt.",
                    "step4_legal": "s 63(1) RTA is strict liability. Defence of defective speedometer not recognised.",
                    "step5_arguments": "Prosecution case strong. Defence case weak due to lack of supporting evidence.",
                    "step6_verdict": "Guilty. Fine SGD 800 + 8 demerit points recommended.",
                },
                preliminary_conclusion="Guilty of speeding under s 63(1) Road Traffic Act.",
                uncertainty_flags={"speedometer_defence_unverified": True},
                confidence_score=92,
            ))

            print(f"[case] Inserted traffic demo case: {CASE_TV_ID}")

        # ── 4. Demo case: Small Claims ────────────────────────────────────────
        if session.get(Case, CASE_SC_ID):
            print(f"[case] Small claims demo case already exists — skipping.")
        else:
            case_sc = Case(
                id=CASE_SC_ID,
                domain=CaseDomain.small_claims,
                domain_id=DOMAIN_SC_ID_ACTUAL,
                title="Lim Wei Xian v TechFix Solutions Pte Ltd — Defective Laptop Repair",
                description=(
                    "Claimant paid SGD 1,200 to Respondent on 3 October 2025 for a logic board "
                    "replacement on his MacBook Pro. The laptop failed again with the same fault "
                    "within 14 days. Respondent refused to refund or re-repair without additional "
                    "charges. Claimant seeks refund of SGD 1,200 plus SGD 300 in consequential "
                    "losses (rental of temporary replacement laptop)."
                ),
                filed_date=date(2026, 1, 20),
                claim_amount=1500.00,
                consent_to_higher_claim_limit=False,
                status=CaseStatus.ready_for_review,
                jurisdiction_valid=True,
                complexity=CaseComplexity.medium,
                route=CaseRoute.proceed_with_review,
                created_by=JUDGE_ID,
                judicial_decision={
                    "verdict": "claimant_succeeds_partial",
                    "reasoning": (
                        "The repair failed within 14 days — well within any reasonable implied "
                        "warranty of satisfactory quality under the Consumer Protection (Fair "
                        "Trading) Act. The Respondent did not adduce evidence that the renewed "
                        "failure was caused by post-repair misuse. The primary claim of SGD 1,200 "
                        "is allowed. The consequential loss of SGD 300 is disallowed as such losses "
                        "must be foreseeable at the time of contract; no evidence was presented "
                        "that Respondent was made aware of the need for a temporary replacement."
                    ),
                    "award_sgd": 1200.00,
                    "interest_rate_percent": 5.33,
                    "interest_from": "2026-01-20",
                    "mitigating_factors": ["Claimant gave Respondent opportunity to re-repair before filing"],
                    "aggravating_factors": ["Respondent made no offer of settlement"],
                    "confidence": 88,
                },
            )
            session.add(case_sc)
            session.flush()

            # Parties
            party_claimant = Party(
                case_id=CASE_SC_ID, name="Lim Wei Xian", role=PartyRole.claimant,
                contact_info={"email": "limweixian@gmail.com", "phone": "+65 9123 4567"}
            )
            party_respondent = Party(
                case_id=CASE_SC_ID, name="TechFix Solutions Pte Ltd", role=PartyRole.respondent,
                contact_info={"uen": "201987654K", "address": "111 North Bridge Road #02-34 Peninsula Plaza"}
            )
            session.add_all([party_claimant, party_respondent])
            session.flush()

            # Documents
            doc_invoice = Document(
                case_id=CASE_SC_ID,
                filename="techfix_repair_invoice_INV2025-3847.pdf",
                file_type="application/pdf",
                uploaded_by=JUDGE_ID,
                uploaded_at=datetime(2026, 1, 20, 9, 30, tzinfo=UTC),
            )
            doc_photos = Document(
                case_id=CASE_SC_ID,
                filename="laptop_fault_photos_oct2025.pdf",
                file_type="application/pdf",
                uploaded_by=JUDGE_ID,
                uploaded_at=datetime(2026, 1, 20, 9, 35, tzinfo=UTC),
            )
            doc_rental = Document(
                case_id=CASE_SC_ID,
                filename="rental_laptop_receipt_oct2025.pdf",
                file_type="application/pdf",
                uploaded_by=JUDGE_ID,
                uploaded_at=datetime(2026, 1, 20, 9, 40, tzinfo=UTC),
            )
            session.add_all([doc_invoice, doc_photos, doc_rental])
            session.flush()

            # Evidence
            session.add_all([
                Evidence(
                    case_id=CASE_SC_ID,
                    document_id=doc_invoice.id,
                    evidence_type=EvidenceType.documentary,
                    strength=EvidenceStrength.strong,
                    admissibility_flags={"authenticated": True, "original_produced": True},
                    linked_claims={"primary_claim": "repair_fee_paid_sgd_1200"},
                ),
                Evidence(
                    case_id=CASE_SC_ID,
                    document_id=doc_photos.id,
                    evidence_type=EvidenceType.documentary,
                    strength=EvidenceStrength.medium,
                    admissibility_flags={"authenticated": True, "metadata_extracted": True},
                    linked_claims={"establishes": "laptop_failed_within_14_days"},
                ),
                Evidence(
                    case_id=CASE_SC_ID,
                    document_id=doc_rental.id,
                    evidence_type=EvidenceType.documentary,
                    strength=EvidenceStrength.medium,
                    admissibility_flags={"authenticated": True},
                    linked_claims={"consequential_loss": "temporary_rental_sgd_300"},
                ),
            ])

            # Facts
            session.add_all([
                Fact(
                    case_id=CASE_SC_ID,
                    event_date=date(2025, 10, 3),
                    description="Claimant paid SGD 1,200 (Invoice INV2025-3847) to Respondent for MacBook Pro logic board replacement.",
                    confidence=FactConfidence.high,
                    status=FactStatus.agreed,
                    source_document_id=doc_invoice.id,
                ),
                Fact(
                    case_id=CASE_SC_ID,
                    event_date=date(2025, 10, 17),
                    description="Laptop displayed the same kernel panic fault within 14 days of repair. Photos taken by Claimant confirm fault code identical to pre-repair fault.",
                    confidence=FactConfidence.high,
                    status=FactStatus.agreed,
                    source_document_id=doc_photos.id,
                ),
                Fact(
                    case_id=CASE_SC_ID,
                    event_date=date(2025, 10, 18),
                    description="Respondent was notified of re-failure and refused to re-repair without charging an additional SGD 600 diagnostic fee.",
                    confidence=FactConfidence.high,
                    status=FactStatus.agreed,
                ),
                Fact(
                    case_id=CASE_SC_ID,
                    event_date=date(2025, 10, 19),
                    description="Claimant rented a replacement laptop from ComputerLand (SGD 300) for the period 19 October – 19 November 2025.",
                    confidence=FactConfidence.high,
                    status=FactStatus.agreed,
                    source_document_id=doc_rental.id,
                ),
                Fact(
                    case_id=CASE_SC_ID,
                    description="Respondent claims the re-failure was caused by liquid damage post-repair but did not produce a liquid damage report or any technical inspection.",
                    confidence=FactConfidence.low,
                    status=FactStatus.disputed,
                ),
            ])

            # Witnesses
            session.add(Witness(
                case_id=CASE_SC_ID,
                name="Lim Wei Xian",
                role="Claimant",
                party_id=party_claimant.id,
                credibility_score=78,
                bias_indicators={"is_party": True},
                simulated_testimony=(
                    "I collected the laptop on 3 October 2025 after paying SGD 1,200. Within "
                    "14 days the same kernel panic appeared. I brought it back immediately. "
                    "TechFix said I must pay another SGD 600. I never spilled anything on it."
                ),
            ))

            # Legal rules
            session.add_all([
                LegalRule(
                    case_id=CASE_SC_ID,
                    statute_name="Consumer Protection (Fair Trading) Act",
                    section="s 12H(1)",
                    verbatim_text=(
                        "Where a consumer requests a supplier to carry out a service and the "
                        "supplier carries out that service, there is an implied guarantee that "
                        "the service will be carried out with reasonable care and skill."
                    ),
                    relevance_score=0.95,
                    application=(
                        "The repair service must be carried out with reasonable care and skill. "
                        "Re-failure within 14 days is prima facie evidence of breach."
                    ),
                ),
                LegalRule(
                    case_id=CASE_SC_ID,
                    statute_name="Small Claims Tribunals Act",
                    section="s 5(1)(b)",
                    verbatim_text="Claim arising from a contract for the provision of services of a value not exceeding the prescribed limit.",
                    relevance_score=0.90,
                    application="Tribunal has jurisdiction. Claim amount SGD 1,500 is within the SGD 20,000 limit.",
                ),
            ])

            # Precedents
            session.add_all([
                Precedent(
                    case_id=CASE_SC_ID,
                    citation="Tan Boon Huat v ValueTech Repairs [SCT 2022/0341]",
                    court="Small Claims Tribunal, Singapore",
                    outcome="Claimant succeeded",
                    reasoning_summary=(
                        "Tribunal found that a repair service that fails within one month raises "
                        "a rebuttable presumption of inadequate workmanship; respondent failed to rebut."
                    ),
                    similarity_score=0.82,
                    source=PrecedentSource.curated,
                ),
            ])

            # Arguments
            session.add_all([
                Argument(
                    case_id=CASE_SC_ID,
                    side=ArgumentSide.claimant,
                    legal_basis=(
                        "Under the CPFTA s 12H implied guarantee, the repair service must be "
                        "carried out with reasonable care and skill. Re-failure within 14 days "
                        "demonstrates the repair was not done with the requisite care. "
                        "Respondent's refusal to remedy without extra payment further breaches "
                        "the implied right to remedy under s 12O CPFTA."
                    ),
                    supporting_evidence={"key_docs": [
                        "techfix_repair_invoice_INV2025-3847.pdf",
                        "laptop_fault_photos_oct2025.pdf",
                    ]},
                    weaknesses="Consequential loss claim (SGD 300) may fail as foreseeability not established.",
                    suggested_questions={"to_respondent": [
                        "Why was no liquid damage report produced?",
                        "What is your standard warranty period for logic board replacements?",
                    ]},
                ),
                Argument(
                    case_id=CASE_SC_ID,
                    side=ArgumentSide.respondent,
                    legal_basis=(
                        "Respondent contends that the re-failure was caused by liquid ingress "
                        "after the repair was completed — an event outside their control. "
                        "The repair itself was carried out to industry standard."
                    ),
                    supporting_evidence={},
                    weaknesses=(
                        "No liquid damage report or technical inspection report produced. "
                        "Burden shifts to Respondent to prove post-repair misuse — not discharged."
                    ),
                    suggested_questions={"to_claimant": [
                        "Can you confirm no liquids were spilled on the device after collection?",
                    ]},
                ),
            ])

            # Hearing analysis
            session.add(HearingAnalysis(
                case_id=CASE_SC_ID,
                reasoning_chain={
                    "step1_intake": "Case classified as small_claims. Jurisdiction valid. Claim SGD 1,500 within SCT limit.",
                    "step2_evidence": "Invoice authenticated. Photos corroborate re-failure. Rental receipt supports consequential loss claim.",
                    "step3_facts": "Repair failed within 14 days — established beyond reasonable doubt.",
                    "step4_legal": "CPFTA s 12H implied guarantee breached. Burden on Respondent to show post-repair misuse — not discharged.",
                    "step5_arguments": "Claimant's primary claim strong. Consequential loss claim weak on foreseeability.",
                    "step6_verdict": "Claimant succeeds on SGD 1,200 primary claim. SGD 300 consequential loss disallowed.",
                },
                preliminary_conclusion=(
                    "Claimant partially succeeds. Award SGD 1,200 plus interest at 5.33% p.a."
                ),
                uncertainty_flags={"liquid_damage_claim_unverified": True},
                confidence_score=88,
            ))

            print(f"[case] Inserted small claims demo case: {CASE_SC_ID}")

        session.commit()
        print("\nDone. Summary:")
        print("  Domains: small_claims, traffic_violation")
        print(f"  Traffic demo case: {CASE_TV_ID}")
        print(f"  Small claims demo case: {CASE_SC_ID}")


if __name__ == "__main__":
    main()
