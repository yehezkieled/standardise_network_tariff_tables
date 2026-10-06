# AER tariff DB verification (worker B): VIC, SA, TAS

- **Verdict:** Not 100% accurate. All 49,063 in-scope rows were checked; 48,415 match (98.68%). 648 rows disagree (620 DB wrong, 28 source ambiguous). Every published price value that is present is correct. The gaps are identity and completeness: 1 high and 6 medium findings. Fix before treating the DB as complete.
- **Ship fixes?** Yes. Fix H1 and M1-M6 in a follow-up before calling the DB complete. Low items can go in the same follow-up.
- **Scope:** https://github.com/yehezkieled/aer-tariff-recon/pull/2, at main `a0576c6`. Distributors: AusNet, CitiPower, Jemena, Powercor, United Energy, SAPN, TasNetworks; FY2023-24 to FY2026-27.
- **Generated:** 2026-10-07 by aer-verify-b; evidence scripts are in the worktree `vf/` (throwaway); raw list in `mismatches.csv`.

## Headline numbers

| Measure | Value |
|---|---|
| Rows in scope (my 7 distributors, all years) | 49,063 |
| Rows checked | 49,063 (100%, 0 skipped) |
| Rows matching source | 48,415 (98.68%) |
| Rows disagreeing: DB wrong | 620 |
| Rows disagreeing: source ambiguous | 28 |
| Verifier misreads | 0 left: every automated mismatch was re-checked at the source, and misreads were set to match with a `manual:` note |
| Missing-row findings (not countable as DB rows) | H1, M3, M5, M6, L1-L3, L12, L13 |
| Charges whose value differs from the source | 0 of 21,691 |

## Findings (ranked)

- Rows = mismatched DB rows attributed to the finding (a row hit by two findings counts once); `-` = the finding is about rows that are missing.

| ID | Sev | Class | Distributor | Years | Table | Finding | DB | Source says | Where | Rows |
|---|---|---|---|---|---|---|---|---|---|---|
| H1 | high | DB wrong (omission) | ausnet | 2023-24..2026-27 | tariff_listing, charge | NASN2S and NASN2P priced rows are missing from every AusNet distributor document (8 listings, ~120 charges) | no listing, no charges | 14-16 non-zero cells per row, e.g. 2024-25 'Distribution 2024-25'!G57 Standing charge 138.51 $/year; 2023-24 PDF p2 NUoS standing 132.87 $/year, peak 18.2526 c/kWh, off-peak 4.6386 c/kWh, monthly peak 8.74 $/kW/mth | AusNet_2024-25_Schedule_of_tariffs_28Mar2024.xlsx rows 57-58; AusNet_Network_Tariff_Schedule_2025-26.xlsx rows 50-51; 2026-27 row 38; AusNet_2023-24_Schedule_of_tariffs_31Mar2023.pdf p2/5/8/11; Annual Pricing Proposal 2023-24 p43-52 | - |
| M1 | medium | DB wrong | ausnet, citipower, powercor, unitedenergy | 2025-26 | tariff_listing, charge, tariff | AER 2025-26 v1 rows for large tariffs are stored under their AER IDs instead of the tariff (12 listings, 48 charges on bogus tariffs 'TD-*26OTH-*') | tariff_id e.g. citipower:TD-CPR26OTH-HV2 | same AER ID in v5 carries code CHV (name identical), e.g. v1 row I190 'High Voltage (opt-in)' | AER_Consolidated_stakeholder_report_2025-26_v1_wayback.xlsx Tariff schedule rows G135 (AusNet), I186-I190 (CitiPower), I956-I962 (Powercor), I1190 and I1192 (United Energy) | 48 |
| M2 | medium | DB wrong | sapn | 2024-25 | tariff, tariff_listing, charge | AER row whose 'Code SA' is '-' becomes tariff sapn:- (10 charges) instead of sapn:ZSN228, which its 'Code CBD' cell names | tariff 'sapn:-' | 'Code SA' = '-', 'Code CBD' = 'ZSN228' (Zone Substation kVA Locational) | AER_Stakeholder_report_SAPN_2024-25_updated17Jul2024.xlsx Tariff schedule!E429:F429 (also rows 507, 585, 663) | 10 |
| M3 | medium | DB wrong (omission) | sapn | 2025-26, 2026-27 | charge / metering_price | 'METERING - Meter Charge' column ($/day) of SAPN's NUoS tables is not ingested for any tariff (3 documents) | no charge and no metering_price row | e.g. RSR 0.0255 $/day, RTOU 0.0255, RESELE 0.0255 | SAPN_2025-26_Pricing_Proposal_Overview_14May2025.pdf p37; initial proposal overview 2025-26; tariff price list 2026-27 | - |
| M4 | medium | DB wrong | sapn | 2024-25..2026-27 | charge (period, unit_std, value_std) | 402 SAPN demand charges in the AER files take 'Annual'/'Monthly' as the billing period; it names the measurement window and SAPN prices per day | period=year/month (inferred), unit_std c/kVA/year? or /month? | SAPN's own lists print the same values as $/kVA/day, e.g. HVAD265 Ann Dmnd Pk 0.3348 = APP 2024-25 p67 '0.3348 $/kVA/day' | aer-consolidated-2025-26-v5 / 2026-27-v5 Tariff schedule (47 rows each); AER stakeholder report SAPN 2024-25 (308 rows) | 388 |
| M5 | medium | DB incomplete | ausnet | 2023-24..2026-27 | charge_step, exception_instance | AusNet inclining-block bound is not recorded: 48 tariff-years (672 block1/block2 charges) have no charge_step and no quantity_blocks instance | no charge_step rows | 'Inclining block 1 c/kWh 1020 kWh/qtr'; 'Inclining block 2 c/kWh kWh balance' (block prices differ in 2023-24, 2024-25 and part of 2025-26) | AusNet schedules Tariff structures!C7:E8 (2024-25, 2026-27); Annual Pricing Proposal 2023-24 p54 | - |
| M6 | medium | DB incomplete | citipower | 2025-26, 2026-27 | tou_schedule, tou_window, tariff_tou | CitiPower residential and small-business windows are not encoded, although the held PDFs print them (Powercor's same-format PDFs are encoded) | 2025-26: 7 schedules; 2026-27: 10 (no CRTOU/CRSTOU, CGTOU, CMGO21, C2U, CRCER, CFS, CFL windows) | 2026-27 Table 4 'CRSTOU Default all days 4pm-9pm / all days 9pm-11am / all days 11am-4pm'; Table 5 'CGTOU workdays 9am-9pm', 'CMGO21 workdays 10am-6pm', 'C2U weekdays 7am-11pm' | citipower-pricing-proposal-2026-27-07may2026 pdf:p5, pdf-ocr:p6, p8; citipower-pricing-proposal-2025-26-31mar2025 pdf:p5, pdf-ocr:p7, p8 (pages rendered and read) | - |
| L1 | low | DB incomplete | ausnet, citipower, jemena, powercor, unitedenergy, sapn | 2024-25 | charge | AER 2024-25 'Tariff schedule 2' (VDO tariffs) block is not ingested; every value equals Tariff schedule 3, so only the VDO/DMO designation is lost | absent | e.g. AusNet I19 = 138.5102 (NEE11 Fixed NUoS) | AER_Stakeholder_report_<DNSP>_2024-25.xlsx Tariff schedule 2 block | - |
| L2 | low | DB wrong (omission) | ausnet | 2026-27 | tariff, tariff_listing, charge | Tariff STSS is absent; only its JSA/network standing charge is numeric | absent | Jurisdictional 2026-27!G65 Standing charge 79.01 $/year | AusNet_Network_Tariff_Schedule_2026-27_20260515.xlsx row 65 | - |
| L3 | low | DB wrong (omission) | sapn | 2023-24..2025-26 | charge | Last data row of 5 SAPN PDF pages is not ingested (the tariff is present on the other basis pages) | absent | e.g. STN788 DUoS p69 'Non-TOU 0.0016 $/kWh, Annual Anytime 0.0407 $/kVA/day' | SAPN APP 2023-24 p69; APP 2024-25 p69; overview 2025-26 p37, p40, p41 | - |
| L4 | low | DB wrong | sapn | 2024-25 | charge (time_band) | 'Mth Dmnd Shld' rows (9) have no time_band | NULL | label means monthly demand shoulder ('BD Shoulder' in SAPN docs) | AER stakeholder report SAPN 2024-25 Tariff schedule!S267 and siblings | 9 |
| L5 | low | DB wrong | jemena | 2024-25..2026-27 | charge (period_inferred) | 57 AER rows with unit 'cents/kVA/Summer' have period=day but period_inferred=0 | period_inferred 0 | printed unit states no day; Jemena prints ¢/kVA/day | e.g. aer-consolidated-2025-26-v1 Tariff schedule!M810 | 57 |
| L6 | low | DB wrong / source ambiguous | tasnetworks | 2023-24, 2024-25 | charge (unit_published) | 45 TAS87/88/97/98 demand rows store unit_published 'c/kW/day', which is not printed (2023-24 header reads 'c/kVA, kW, lamp watt/day') | 'c/kW/day' | header 'Demand rates c/kVA, kW, lamp watt/day' | tasnetworks SCS 2023-24 p7/10/13; SCS 2024-25 | 45 |
| L7 | low | DB wrong | jemena | 2025-26 | tariff_listing (code_published) | 10 rules-only F-code listings carry code_published although the PDF never prints the codes | F100 ... F300 | footnote a: 'Tariffs starting with "F" ... ended on 1 November 2024' | jemena-network-tariff-schedule-2025-26 p10 | 10 |
| L8 | low | DB wrong | ausnet | 2023-24, 2024-25 | tariff_listing (name_published) | 18 trial listings append ' (tariff trial)' to name_published, which is not printed | 'CPD+ (tariff trial)' | 'CPD+' | ausnet-annual-pricing-proposal-2023-24 p21; 2024-25 trial table | 18 |
| L9 | low | DB wrong | ausnet, citipower, powercor, unitedenergy | 2023-24..2026-27 | tariff (identity_basis), exception_instance | NASN2P, CFTUOS, PFTUOS, UFTUOS have identity_basis aer_label and 7 aer_only_tariff instances, but the distributors print these codes | aer_label; aer_only_tariff | e.g. 'Network 2025-26'!C51 'NASN2S'; CitiPower 2026-27 Table 6 'CFTUOS' | AusNet schedules; CP/PAL/UE 2026-27 pricing proposals pdf-ocr:p8 | 11 |
| L10 | low | DB wrong | sapn | 2024-25..2026-27 | exception_instance | 46 aer_missing_tariff instances are false: the AER prints the CBD/'NE' codes on the base tariff's row | aer_missing_tariff | H1034 'LBAD' + Code CBD 'LBADCBD'; F1022 'RSRNE' (Other identifier) on the RSR row | AER_Consolidated_stakeholder_report_2025-26_v5.xlsx rows 1022-1045; SAPN 2024-25 report F429 | 46 |
| L11 | low | DB wrong | sapn | 2024-25 | tariff | Orphan tariffs sapn:HVBGFSA and sapn:LBGFSA (no listing, alias or relation) | tariff rows | AER labels 'HVBGF-SA'/'LBGF-SA' are listed under HVBGF/LBGF | AER stakeholder report SAPN 2024-25 | 2 |
| L12 | low | DB incomplete | ausnet, citipower, powercor | 2026-27 | charge_step | 1 kWh/day BEL steps missing for AusNet RCER11 and CitiPower/Powercor CRCER, CFS, PRCER, PFS (United Energy has them) | no charge_step | '^ Includes a BEL of 1 kWh per day'; 'BEL ... multiplying 1 kWh per day by the number of days' | AusNet 2026-27 Tariff structures!E97, B106; CP/PAL 2026-27 PDFs pdf-ocr:p5-p8 | - |
| L13 | low | DB incomplete | citipower, powercor, unitedenergy | 2024-25..2026-27 | tariff_relation, exception_instance | AER large-customer codes (CHV, CLLV, HV, LLV, HVKVATOU, ...) have no relation to the distributor codes (CHV1/CHV2 ...), so 32 demand and 48 TOU tariff-years cannot reach their windows or demand rules, and tou_definition_missing is never raised for AER-only tariff-years | no tariff_relation, no tariff_tou, no instance | AER 'High Voltage (opt-in)' CHV vs distributor 'CHV1 CHV2' rows with the same windows | aer-consolidated-2025-26-v5 Tariff schedule; citipower 2025-26 PDF pdf-ocr:p8 | - |
| L14 | low | DB imprecise | all 7 | all | charge (includes_metering) | 2,820 distributor fixed charges have includes_metering 'unknown'; 763/763 comparable values equal the metering-excluded AER value | unknown | equal to AER values | cross-source comparison | - |
| L15 | low | docs inconsistent | - | - | docs/tariffdb.md | Docs list time_band values 'off_peak'; data uses 'offpeak', 'super_offpeak' | 'offpeak' | 'off_peak' | docs/tariffdb.md charge table | - |
| L16 | low | DB wrong | tasnetworks | 2023-24 | tariff_listing | TAS101, TAS31, TAS87, TAS92, TAS93, TAS97 each split into two listings in one document (values correct) | 2 listings per tariff | Table 1 'Residential' vs Tables 2-3 'Residential low voltage' | tasnetworks SCS 2023-24 p7, p10, p13 | - |
| S1 | low | source ambiguous | ausnet | 2023-24..2026-27 | tou_schedule (time_basis) | 8 schedules stated in 'ADST' are stored as daylight_time; AusNet's glossary defines 'Local time' as daylight saving time, so ADST may mean local time | daylight_time | '3:00PM to 9:00PM ADST'; glossary 'Local time  Daylight savings time in accordance with the Victorian Government's requirements' | AusNet schedules Tariff structures!E13/E64 (2026-27); Annual Pricing Proposal 2023-24 p39-40 | - |
| S2 | low | source ambiguous | ausnet | 2026-27 | tariff_demand_rule | Transitional NAT56/NAT75 link to capacity and critical-peak rules by structure number but print no demand prices (4 rows) | linked | NAT56 row: blank Capacity and Critical peak cells | AusNet 2026-27 schedule Network 2026-27!D51, D54 | 4 |
| S3 | low | source ambiguous | tasnetworks | all | charge | 139 Locational TUoS node charges are attached to TAS15 only; the guide says they also apply to ITC customers | TAS15 only | 'Locational TUoS charges for ... TAS15 ... and ITC ... will apply' | Tas price guide 2023-24 p63-65 | - |
| S4 | low | source ambiguous | tasnetworks | all | charge (charge_type) | 21 TASUMSSL 'c/lamp watt/day' rows are typed fixed | fixed | 'All demand' / 'Lamps dmnd' | Tas price guide 2023-24 p59 | - |
| S5 | low | source ambiguous | unitedenergy | 2026-27 | tou_window, tariff_tou | KEVC trial figure: 9pm-12am off-peak segment and the EVUTOU link are deliberately left out (the DB note says so) | not encoded | Figure 4: unlabelled segment after 9pm shaded like off-peak | ue-pricing-proposal-2026-27 pdf:p14 | - |
| S6 | low | observation | citipower, powercor | 2024-25..2026-27 | rule quotes | About 180 rule quotes from image-only pages are OCR transcriptions, not verbatim; every quoted fact was confirmed on the rendered page | OCR text | page image | 13 pdf-ocr pages | - |

## Coverage by table (all 7 distributors)

| Table | Checked | Matched | Mismatched | Of matches: token-bag quote | Of matches: manual visual |
|---|---|---|---|---|---|
| charge | 21691 | 21134 | 557 | 0 | 14 |
| metering_price | 161 | 161 | 0 | 0 | 0 |
| charge_step | 4 | 4 | 0 | 0 | 0 |
| tou_schedule | 329 | 329 | 0 | 1 | 42 |
| tou_window | 1003 | 1003 | 0 | 10 | 80 |
| tou_window_month | 10490 | 10490 | 0 | 0 | 0 |
| tariff_tou | 1470 | 1470 | 0 | 358 | 79 |
| demand_rule | 303 | 303 | 0 | 33 | 35 |
| tariff_demand_rule | 1161 | 1157 | 4 | 0 | 0 |
| eligibility_rule | 6297 | 6297 | 0 | 638 | 30 |
| tariff | 421 | 415 | 6 | 0 | 0 |
| tariff_listing | 2435 | 2407 | 28 | 0 | 2 |
| listing_flag | 1358 | 1358 | 0 | 0 | 0 |
| tariff_alias | 362 | 362 | 0 | 0 | 0 |
| tariff_relation | 76 | 76 | 0 | 0 | 12 |
| price_adjustment | 0 | 0 | 0 | 0 | 0 |
| price_adjustment_tariff | 0 | 0 | 0 | 0 | 0 |
| exception_instance | 1303 | 1250 | 53 | 0 | 12 |
| document_coverage | 58 | 58 | 0 | 0 | 0 |
| source_document | 67 | 67 | 0 | 0 | 0 |
| document_ingestion | 74 | 74 | 0 | 0 | 0 |
| **total** | **49063** | **48415** | **648** | 1040 | 306 |

## Coverage by distributor x table x year

- Cell format: checked/matched/mismatched; `-` = no rows exist.
- `all` = rows without a single year (tariff identities, year-less exceptions).
- price_adjustment and price_adjustment_tariff have 0 rows for these distributors. That is correct: distributor fixed charges equal the AER values, so no metering adder applies.

### ausnet

| Table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 1674/1674/0 | 1653/1653/0 | 1125/1120/5 | 770/770/0 | - |
| metering_price | - | 5/5/0 | 20/20/0 | 10/10/0 | - |
| tou_schedule | 14/14/0 | 16/16/0 | 14/14/0 | 15/15/0 | - |
| tou_window | 57/57/0 | 63/63/0 | 57/57/0 | 56/56/0 | - |
| tou_window_month | 562/562/0 | 634/634/0 | 562/562/0 | 550/550/0 | - |
| tariff_tou | 78/78/0 | 80/80/0 | 70/70/0 | 65/65/0 | - |
| demand_rule | 3/3/0 | 5/5/0 | 3/3/0 | 3/3/0 | - |
| tariff_demand_rule | 34/34/0 | 58/58/0 | 31/31/0 | 37/33/4 | - |
| eligibility_rule | 572/572/0 | 703/703/0 | 152/152/0 | 122/122/0 | - |
| tariff | - | - | - | - | 94/93/1 |
| tariff_listing | 143/136/7 | 172/161/11 | 190/190/0 | 90/90/0 | - |
| listing_flag | 87/87/0 | 80/80/0 | 117/117/0 | 21/21/0 | - |
| tariff_alias | - | - | 127/127/0 | - | - |
| exception_instance | 111/111/0 | 107/105/2 | 90/89/1 | 45/44/1 | - |
| document_coverage | - | - | 5/5/0 | 3/3/0 | - |
| source_document | 2/2/0 | 3/3/0 | 1/1/0 | 1/1/0 | - |
| document_ingestion | 2/2/0 | 3/3/0 | 1/1/0 | 1/1/0 | - |

### citipower

| Table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 389/389/0 | 404/404/0 | 94/78/16 | 238/238/0 | - |
| metering_price | - | 3/3/0 | 14/14/0 | 7/7/0 | - |
| tou_schedule | 18/18/0 | 14/14/0 | 7/7/0 | 10/10/0 | - |
| tou_window | 47/47/0 | 35/35/0 | 13/13/0 | 16/16/0 | - |
| tou_window_month | 512/512/0 | 380/380/0 | 140/140/0 | 162/162/0 | - |
| tariff_tou | 41/41/0 | 36/36/0 | 17/17/0 | 25/25/0 | - |
| demand_rule | 9/9/0 | 9/9/0 | 8/8/0 | 9/9/0 | - |
| tariff_demand_rule | 26/26/0 | 25/25/0 | 16/16/0 | 19/19/0 | - |
| eligibility_rule | 152/152/0 | 146/146/0 | 79/79/0 | 108/108/0 | - |
| tariff | - | - | - | - | 39/38/1 |
| tariff_listing | 42/42/0 | 57/57/0 | 30/30/0 | 38/38/0 | - |
| listing_flag | 12/12/0 | 12/12/0 | 15/15/0 | 4/4/0 | - |
| tariff_alias | - | - | 26/26/0 | - | - |
| tariff_relation | 1/1/0 | 1/1/0 | 1/1/0 | 1/1/0 | - |
| exception_instance | 20/20/0 | 41/41/0 | 21/21/0 | 37/36/1 | - |
| document_coverage | - | - | 5/5/0 | 3/3/0 | - |
| source_document | 2/2/0 | 3/3/0 | 1/1/0 | 2/2/0 | - |
| document_ingestion | 2/2/0 | 3/3/0 | 1/1/0 | 2/2/0 | - |

### jemena

| Table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 1680/1680/0 | 826/800/26 | 367/341/26 | 160/155/5 | - |
| metering_price | - | 4/4/0 | 16/16/0 | 8/8/0 | - |
| tou_schedule | 22/22/0 | 22/22/0 | 11/11/0 | 11/11/0 | - |
| tou_window | 58/58/0 | 58/58/0 | 29/29/0 | 35/35/0 | - |
| tou_window_month | 680/680/0 | 680/680/0 | 340/340/0 | 412/412/0 | - |
| tariff_tou | 224/224/0 | 224/224/0 | 99/99/0 | 53/53/0 | - |
| demand_rule | 22/22/0 | 22/22/0 | 11/11/0 | 9/9/0 | - |
| tariff_demand_rule | 140/140/0 | 140/140/0 | 63/63/0 | 30/30/0 | - |
| eligibility_rule | 542/542/0 | 588/588/0 | 205/205/0 | 108/108/0 | - |
| tariff | - | - | - | - | 53/53/0 |
| tariff_listing | 100/100/0 | 133/133/0 | 98/88/10 | 40/40/0 | - |
| listing_flag | 46/46/0 | 71/71/0 | 69/69/0 | 4/4/0 | - |
| tariff_alias | - | 18/18/0 | 48/48/0 | - | - |
| tariff_relation | 2/2/0 | 2/2/0 | 11/11/0 | 1/1/0 | - |
| exception_instance | 12/12/0 | 47/47/0 | 29/29/0 | 16/16/0 | - |
| document_coverage | - | - | 5/5/0 | 3/3/0 | - |
| source_document | 2/2/0 | 3/3/0 | 1/1/0 | 1/1/0 | - |
| document_ingestion | 2/2/0 | 3/3/0 | 1/1/0 | 1/1/0 | - |

### powercor

| Table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 189/189/0 | 408/408/0 | 298/279/19 | 238/238/0 | - |
| metering_price | - | 3/3/0 | 14/14/0 | 7/7/0 | - |
| tou_schedule | - | 15/15/0 | 15/15/0 | 19/19/0 | - |
| tou_window | - | 38/38/0 | 38/38/0 | 48/48/0 | - |
| tou_window_month | - | 416/416/0 | 416/416/0 | 462/462/0 | - |
| tariff_tou | - | 38/38/0 | 38/38/0 | 35/35/0 | - |
| demand_rule | 4/4/0 | 9/9/0 | 9/9/0 | 8/8/0 | - |
| tariff_demand_rule | 23/23/0 | 25/25/0 | 25/25/0 | 18/18/0 | - |
| eligibility_rule | 24/24/0 | 163/163/0 | 168/168/0 | 105/105/0 | - |
| tariff | - | - | - | - | 43/42/1 |
| tariff_listing | 19/19/0 | 58/58/0 | 54/54/0 | 38/38/0 | - |
| listing_flag | 4/4/0 | 15/15/0 | 28/28/0 | 4/4/0 | - |
| tariff_alias | - | - | 25/25/0 | - | - |
| tariff_relation | - | 1/1/0 | 1/1/0 | 1/1/0 | - |
| exception_instance | 17/17/0 | 27/27/0 | 33/33/0 | 18/17/1 | - |
| document_coverage | - | - | 5/5/0 | 3/3/0 | - |
| source_document | 2/2/0 | 3/3/0 | 2/2/0 | 2/2/0 | - |
| document_ingestion | 2/2/0 | 3/3/0 | 2/2/0 | 2/2/0 | - |

### unitedenergy

| Table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 250/250/0 | 220/220/0 | 66/58/8 | 229/229/0 | - |
| metering_price | - | 4/4/0 | 14/14/0 | 8/8/0 | - |
| charge_step | - | - | - | 4/4/0 | - |
| tou_schedule | 15/15/0 | 3/3/0 | 10/10/0 | 17/17/0 | - |
| tou_window | 36/36/0 | 7/7/0 | 23/23/0 | 48/48/0 | - |
| tou_window_month | 376/376/0 | 84/84/0 | 236/236/0 | 456/456/0 | - |
| tariff_tou | 30/30/0 | 5/5/0 | 21/21/0 | 32/32/0 | - |
| demand_rule | 11/11/0 | 2/2/0 | 7/7/0 | 10/10/0 | - |
| tariff_demand_rule | 18/18/0 | 2/2/0 | 14/14/0 | 20/20/0 | - |
| eligibility_rule | 113/113/0 | 33/33/0 | 65/65/0 | 145/145/0 | - |
| tariff | - | - | - | - | 32/31/1 |
| tariff_listing | 34/34/0 | 29/29/0 | 22/22/0 | 39/39/0 | - |
| listing_flag | 8/8/0 | 6/6/0 | 11/11/0 | 5/5/0 | - |
| tariff_alias | - | 2/2/0 | 22/22/0 | 2/2/0 | - |
| tariff_relation | - | - | 1/1/0 | 1/1/0 | - |
| exception_instance | 6/6/0 | 28/28/0 | 16/16/0 | 24/23/1 | - |
| document_coverage | - | - | 5/5/0 | 3/3/0 | - |
| source_document | 2/2/0 | 3/3/0 | 2/2/0 | 2/2/0 | - |
| document_ingestion | 2/2/0 | 3/3/0 | 2/2/0 | 2/2/0 | - |

### sapn

| Table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 2224/2224/0 | 3231/2918/313 | 2393/2346/47 | 1094/1047/47 | - |
| metering_price | - | - | 1/1/0 | 1/1/0 | - |
| tou_schedule | 14/14/0 | 14/14/0 | 28/28/0 | - | - |
| tou_window | 55/55/0 | 55/55/0 | 104/104/0 | - | - |
| tou_window_month | 556/556/0 | 556/556/0 | 1054/1054/0 | - | - |
| tariff_tou | 60/60/0 | 60/60/0 | 126/126/0 | - | - |
| demand_rule | 25/25/0 | 25/25/0 | 62/62/0 | 5/5/0 | - |
| tariff_demand_rule | 64/64/0 | 64/64/0 | 124/124/0 | 132/132/0 | - |
| eligibility_rule | 435/435/0 | 506/506/0 | 500/500/0 | 204/204/0 | - |
| tariff | - | - | - | - | 134/132/2 |
| tariff_listing | 182/182/0 | 286/286/0 | 210/210/0 | 115/115/0 | - |
| listing_flag | 144/144/0 | 244/244/0 | 215/215/0 | 52/52/0 | - |
| tariff_alias | - | 16/16/0 | 28/28/0 | - | - |
| tariff_relation | - | 1/1/0 | 21/21/0 | 19/19/0 | - |
| exception_instance | 62/62/0 | 126/116/10 | 161/144/17 | 125/106/19 | - |
| document_coverage | - | - | 3/3/0 | 5/5/0 | - |
| source_document | 2/2/0 | 4/4/0 | 2/2/0 | 1/1/0 | - |
| document_ingestion | 2/2/0 | 4/4/0 | 2/2/0 | 1/1/0 | - |

### tasnetworks

| Table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 346/322/24 | 431/410/21 | 385/385/0 | 309/309/0 | - |
| metering_price | - | 1/1/0 | 14/14/0 | 7/7/0 | - |
| tou_schedule | 5/5/0 | - | - | - | - |
| tou_window | 27/27/0 | - | - | - | - |
| tou_window_month | 264/264/0 | - | - | - | - |
| tariff_tou | 13/13/0 | - | - | - | - |
| demand_rule | 13/13/0 | - | - | - | - |
| tariff_demand_rule | 13/13/0 | - | - | - | - |
| eligibility_rule | 144/144/0 | 111/111/0 | 52/52/0 | 52/52/0 | - |
| tariff | - | - | - | - | 26/26/0 |
| tariff_listing | 46/46/0 | 50/50/0 | 72/72/0 | 48/48/0 | - |
| listing_flag | 43/43/0 | 7/7/0 | 29/29/0 | 5/5/0 | - |
| tariff_alias | - | - | 48/48/0 | - | - |
| tariff_relation | 10/10/0 | - | - | - | - |
| exception_instance | 10/10/0 | 25/25/0 | 21/21/0 | 28/28/0 | - |
| document_coverage | - | - | 5/5/0 | 5/5/0 | - |
| source_document | 2/2/0 | 2/2/0 | 1/1/0 | 1/1/0 | - |
| document_ingestion | 2/2/0 | 2/2/0 | 1/1/0 | 1/1/0 | - |

### AER (shared)

| Table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| source_document | - | - | 6/6/0 | 6/6/0 | - |
| document_ingestion | - | 7/7/0 | 6/6/0 | 6/6/0 | - |

## What was verified and found correct

| Area | Result | Evidence |
|---|---|---|
| Charge values (value_published, raw, unit, label, basis, GST, locator/page/cell) | 21,691 / 21,691 values equal the source | independent XLSX cell reads with Excel display rounding; PDF word geometry by row and column |
| Charge completeness | no unclaimed priced cells except H1, M3, L1-L3 | every numeric token in a tariff row of the source was accounted for |
| Effective dates | match document year | fin_year to 1 Jul-30 Jun |
| metering_price | 161 / 161; 0 source rows missing | AER Metering sheets and 2024-25 Tariff schedule 1 |
| TOU windows | 1,003 windows: times, day type and months agree with quotes; 10,490 month rows equal the window month lists | quote parse; 154 full-day schedules tile 24h with no gap; no energy-period overlap |
| DST / time basis | 329 / 329 schedules supported by the source (local, AEST standard, or not stated); see S1 | Tas guide p9 'All times ... AEST'; CP/PAL/UE notes 'All times are local time, except for C2U/PL2/UNMET (AEST)'; SAPN 'Local Time (CST/CDST)'; distributor iana_timezone and observes_dst correct for VIC/SA/TAS |
| demand_rule | 303 / 303: measure, interval, aggregation, minimum and window agree | Tas guide Table 36 p66; UE 2026-27 p11; SAPN tables |
| eligibility_rule | 6,297 / 6,297: value, unit, operator and category agree with quotes | symbol-font ≥/≤ and split words re-read by hand |
| price_status | 58 / 58 document_coverage rows agree with the AER landing-page changelogs | 2025-26: v1-v2 proposed, v3 approved VIC/TAS, SA approved in v5; 2026-27: SA/TAS approved v2, VIC approved v4 |
| Distributor proposals labelled published | prices equal the approved AER values: Powercor 2025-26 22/22; CP/PAL/UE 2026-27 37/35/37 components. SAPN differs only in the RESELE/SBELE export peak (AER -12.25 vs SAPN 1.0, a genuine source difference) | cross-source comparison |
| Provenance | 67 / 67 source_document sha256 equal the files; 74 / 74 ingestion counts equal the tables | sha256 recomputed |
| exception_instance | 1,250 / 1,303 claims hold; 53 false (L9, L10) | each code re-derived from sources or tables |
| Listings, flags, aliases, relations | 2,407 / 2,435 listings; 1,358 / 1,358 flags; 362 / 362 aliases; 76 / 76 relations | code, name, class and region at the locator; flag evidence quotes |

## Method

- Every source under `sources/` was re-read independently with pymupdf and openpyxl. No repository parser or script was used to produce expected values.
- XLSX: the value is read at the locator cell and rendered with its number format (15 significant digits, half-up), then compared with value_published and value_raw.
- PDF: words are grouped into rows by y-centre and assigned to columns by header x-position. The value is checked in the code row, and each label must sit in a consistent column.
- Completeness: every numeric token in a tariff row must be claimed by a DB charge; unclaimed tokens were reviewed by hand.
- Quotes: checked in order of strength: normalised substring, then ordered tokens within a row, then a ±45 pt band, then all words on the page (token-bag, order not checked).
- Semantics: units and periods, charge type, band and season, TOU times, months and day types, time basis, demand measure and interval, eligibility operators and values, and link sanity.
- Exceptions: each code is re-derived (AER presence from the AER sheets, distributor presence from distributor text, and the other claims from tables or cells).
- Mismatch triage: every automated mismatch was re-checked at the source first. Verifier artefacts were reclassified as match with a `manual:` note; the rest are findings.

## Caveats

- 1040 matches rest on the token-bag quote check (every word is on the page but order is not checked). These are mostly table-layout quotes; their structured values were checked separately.
- 306 matches were confirmed by a manual reading, including 13 image-only pages rendered at 100-110 dpi and read by eye (S6).
- Semantic checks of the charge classification compared a keyword classifier with the DB. Differences that are DB conventions (period day for $/year fixed, a `?` suffix for inferred periods) were accepted.
- AER-side values for other distributors are out of scope; another worker covers them.

## Recommendations

- H1: ingest the NASN2S/NASN2P rows from every AusNet schedule and proposal, and drop their aer_only_tariff instances.
- M1/M2: resolve v1 AER IDs through the v5 ID-to-code map, and use the Code CBD cell when Code SA is "-".
- M3: add SAPN 'Meter Charge' ($/day) as charges or metering_price rows.
- M4: for SAPN AER demand rows, set period day, as SAPN's own schedules state. Then recompute unit_std and value_std.
- M5/L12: add charge_step rows for the AusNet 1020 kWh/qtr blocks and for every 1 kWh/day BEL tariff.
- M6: encode the CitiPower 2025-26 and 2026-27 Table 4/5 windows, as was done for Powercor.
- L10/L13: model AER shared rows (Code CBD, Other identifier, unnumbered large codes) as joint labels or relations. Raise tou_definition_missing for AER-only tariff-years too.
- Append-only rule: H1, M3, M5, M6, L1-L3 and L12-L13 add rows. L9-L10 change exception_instance, which is derived and free to change. M1, M2, M4 and L4-L8 edit existing charge or tariff_listing rows, which `build.py --check-append-only` rejects. AGENTS.md sets the default: "A correction is a new source document version, not an in-place change". The follow-up should take that route.
- Ship decision: do not treat FY2023-27 data for these 7 distributors as complete or 100% accurate until H1 and M1-M6 are fixed. The values that are present can be used.
