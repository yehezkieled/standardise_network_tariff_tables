# aer-verify-a: independent verification of the tariff DB (NSW, ACT, QLD, NT)

**Verdict:** NOT 100% accurate: values are faithful (0 wrong values in 28,698 charges), but 95 rows are wrong (labels/type, units, season vocabulary), 52 rows rest on ambiguous sources, and printed prices and tariffs are missing (Ausgrid and Evoenergy metering columns, Endeavour All-Time export, 13 unpriced Endeavour and 5 unpriced Essential codes).

## Scope
- DB: https://github.com/yehezkieled/aer-tariff-recon/pull/2 at commit a0576c6 (`data/tariffdb/tables/*.csv`); repo not modified (`git status` clean)
- Distributors: ausgrid, endeavour, essential, evoenergy, energex, ergon, powerwater; FY2023-24 to FY2026-27
- Independence: no repo parser or script used to produce expected values; every value re-read from `sources/` with my own throwaway scripts (PyMuPDF, python-calamine, openpyxl for number_format only, pandas)
- Counting: every DB row for these distributors was checked once per table; "ambiguous" rows are counted separately, not as matched

## Totals
| metric | rows |
|---|---|
| checked | 52832 |
| matched | 52685 |
| DB wrong | 95 |
| source ambiguous | 52 |
| printed items missing from DB (cells / codes) | 51 + 83 + 32 + 18 (+ 390 repeated printings, GST-incl tables) |

## What was checked, and how
| table (rows) | checks | method |
|---|---|---|
| charge (28,698 = 25,512 xlsx + 3,186 pdf / pdf-ocr) | value, unit, value_std, column semantics (label / band / season / type from header), gst, page / locator | calamine + openpyxl number_format with 15-sig half-up display rounding; PyMuPDF word coordinates with column clustering; row-aware extractors for the Evoenergy statement and schedule; visual render for ambiguous cells; OCR rows re-derived from the statement and the NUOS = DUOS + TUOS + JS sum (94/94) |
| metering_price (189) | value and unit at cell | direct cell read |
| charge_step (86) | bounds, inclusivity, quote at locator | quote search; bound numbers inside quote; block1.upper = block2.lower |
| tou_schedule / tou_window / tou_window_month / tariff_tou (250 / 737 / 7,704 / 527) | quote at locator; start/end times appear in quote; 24h tiling per schedule x day x month (1,914 combos); month rows = expansion of months; DST basis vs source wording | independent text search + minute-level tiling; Essential grid checked visually (30/30) |
| demand_rule / tariff_demand_rule (232 / 445) | quote; kW vs kVA vs charge units; interval; aggregation; season join | regex on quote + join to charges |
| eligibility_rule (4,438) | quote at locator; 1,246 numeric thresholds (value, unit scaling, operator) | text search + regex |
| tariff / tariff_listing / listing_flag / tariff_alias / tariff_relation (504 / 2,842 / 3,097 / 621 / 91) | code, name, class at locator; every flag's evidence quote; alias label present in its document; relation endpoints in quote or locator row | text search; compound "(column ...)" evidence parsed |
| price_adjustment / _tariff (14 / 303) | amount = $/yr x 100 / 365; every NUoS charge delta (distributor - AER) recomputed | residual <= 0.005 c/day (Essential prints 2 dp); LFiT exact |
| exception_instance (1,939) | 1,073 source-backed instances re-checked at the source (rounding, repeated cells, page offsets, blocks, placeholders, flags, AER-missing / only); 866 derived instances checked for internal consistency (TOU-missing 398/398, season months not stated, etc.) | cell display recompute; DB joins |
| document_coverage / source_document price_status (66 / 49) | per-version status rebuilt from the landing-page changelog text; 600 proposed_price flags | 66/66 and 600/600 match; 7 AER 2024-25 statuses unsupported |
| completeness | every printed tariff code on the PDF price pages (658 codes, 60 pages) vs tariff_listing; every non-zero xlsx cell in DB rows; every xlsx column with values | regex code scan + cell and column scans |

## Coverage per table (all distributors, all years)
| table | checked | matched | mismatched | ambiguous |
|---|---|---|---|---|
| charge | 28698 | 28578 | 75 | 45 |
| metering_price | 189 | 189 | 0 | 0 |
| charge_step | 86 | 86 | 0 | 0 |
| tou_schedule | 250 | 250 | 0 | 0 |
| tou_window | 737 | 737 | 0 | 0 |
| tou_window_month | 7704 | 7704 | 0 | 0 |
| tariff_tou | 527 | 527 | 0 | 0 |
| demand_rule | 232 | 232 | 0 | 0 |
| tariff_demand_rule | 445 | 425 | 20 | 0 |
| eligibility_rule | 4438 | 4438 | 0 | 0 |
| tariff | 504 | 504 | 0 | 0 |
| tariff_listing | 2842 | 2842 | 0 | 0 |
| listing_flag | 3097 | 3097 | 0 | 0 |
| tariff_alias | 621 | 621 | 0 | 0 |
| tariff_relation | 91 | 91 | 0 | 0 |
| price_adjustment | 14 | 14 | 0 | 0 |
| price_adjustment_tariff | 303 | 303 | 0 | 0 |
| exception_instance | 1939 | 1939 | 0 | 0 |
| document_coverage | 66 | 66 | 0 | 0 |
| source_document | 49 | 42 | 0 | 7 |

## Coverage per distributor x table x year
- Cell format: checked / matched / mismatched (+ambiguous?)

### ausgrid
| table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 480/472/8 | 355/354/1 | 269/268/1 | 197/196/1 | - |
| metering_price | - | 1/1/0 | 10/10/0 | 5/5/0 | - |
| tou_schedule | 2/2/0 | 1/1/0 | - | - | - |
| tou_window | 2/2/0 | 2/2/0 | - | - | - |
| tou_window_month | 12/12/0 | 24/24/0 | - | - | - |
| tariff_tou | 17/17/0 | 1/1/0 | - | - | - |
| demand_rule | 5/5/0 | 7/7/0 | 7/7/0 | 8/8/0 | - |
| tariff_demand_rule | 24/24/0 | 28/28/0 | 28/28/0 | 29/29/0 | - |
| eligibility_rule | 95/95/0 | 139/139/0 | 121/121/0 | 140/140/0 | - |
| tariff | - | - | - | - | 49/49/0 |
| tariff_listing | 29/29/0 | 68/68/0 | 91/91/0 | 65/65/0 | - |
| listing_flag | 12/12/0 | 12/12/0 | 35/35/0 | 10/10/0 | - |
| tariff_alias | - | - | 58/58/0 | - | - |
| tariff_relation | 4/4/0 | - | - | - | - |
| exception_instance | 42/42/0 | 44/44/0 | 44/44/0 | 50/50/0 | - |
| document_coverage | - | - | 5/5/0 | 5/5/0 | - |
| source_document | 2/2/0 | 2/1/0 (+1?) | 1/1/0 | 1/1/0 | - |

### endeavour
| table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 372/372/0 | 417/417/0 | 253/253/0 | 172/172/0 | - |
| metering_price | - | 6/6/0 | 13/13/0 | 7/7/0 | - |
| tou_schedule | 3/3/0 | 3/3/0 | 3/3/0 | 3/3/0 | - |
| tou_window | 13/13/0 | 16/16/0 | 16/16/0 | 16/16/0 | - |
| tou_window_month | 144/144/0 | 168/168/0 | 168/168/0 | 168/168/0 | - |
| tariff_tou | 15/15/0 | 25/25/0 | 18/18/0 | 18/18/0 | - |
| demand_rule | 2/2/0 | 3/3/0 | 2/2/0 | 2/2/0 | - |
| tariff_demand_rule | 7/7/0 | 11/11/0 | 8/8/0 | 8/8/0 | - |
| eligibility_rule | 95/95/0 | 183/183/0 | 104/104/0 | 104/104/0 | - |
| tariff | - | - | - | - | 55/55/0 |
| tariff_listing | 26/26/0 | 65/65/0 | 63/63/0 | 42/42/0 | - |
| listing_flag | 11/11/0 | 108/108/0 | 63/63/0 | 39/39/0 | - |
| tariff_alias | - | - | 42/42/0 | - | - |
| tariff_relation | - | 37/37/0 | 6/6/0 | 6/6/0 | - |
| price_adjustment | - | 1/1/0 | 1/1/0 | 1/1/0 | - |
| price_adjustment_tariff | - | 11/11/0 | 11/11/0 | 11/11/0 | - |
| exception_instance | 7/7/0 | 55/55/0 | 12/12/0 | 2/2/0 | - |
| document_coverage | - | - | 5/5/0 | 5/5/0 | - |
| source_document | 2/2/0 | 2/1/0 (+1?) | 1/1/0 | 1/1/0 | - |

### essential
| table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 862/862/0 | 782/781/0 (+1?) | 340/339/0 (+1?) | 225/224/0 (+1?) | - |
| metering_price | - | 1/1/0 | 2/2/0 | 1/1/0 | - |
| tou_schedule | - | 14/14/0 | 7/7/0 | 7/7/0 | - |
| tou_window | - | 60/60/0 | 30/30/0 | 30/30/0 | - |
| tou_window_month | - | 720/720/0 | 360/360/0 | 360/360/0 | - |
| tariff_tou | - | 83/83/0 | 43/43/0 | 43/43/0 | - |
| demand_rule | 5/5/0 | 6/6/0 | 6/6/0 | 6/6/0 | - |
| tariff_demand_rule | 10/10/0 | 30/30/0 | 27/27/0 | 27/27/0 | - |
| eligibility_rule | 76/76/0 | 162/162/0 | 67/67/0 | 67/67/0 | - |
| tariff | - | - | - | - | 54/54/0 |
| tariff_listing | 48/48/0 | 106/106/0 | 89/89/0 | 64/64/0 | - |
| listing_flag | 52/52/0 | 135/135/0 | 105/105/0 | 70/70/0 | - |
| tariff_alias | - | - | 50/50/0 | - | - |
| tariff_relation | 9/9/0 | 12/12/0 | 6/6/0 | 6/6/0 | - |
| price_adjustment | - | 1/1/0 | 1/1/0 | 1/1/0 | - |
| price_adjustment_tariff | - | 17/17/0 | 16/16/0 | 16/16/0 | - |
| exception_instance | 191/191/0 | 31/31/0 | 23/23/0 | 20/20/0 | - |
| document_coverage | - | - | 5/5/0 | 5/5/0 | - |
| source_document | 2/2/0 | 3/2/0 (+1?) | 1/1/0 | 1/1/0 | - |

### evoenergy
| table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 1038/1016/4 (+18?) | 562/562/0 | 398/398/0 | 430/430/0 | - |
| metering_price | - | - | 8/8/0 | 4/4/0 | - |
| charge_step | 12/12/0 | 12/12/0 | 12/12/0 | 24/24/0 | - |
| tou_schedule | 10/10/0 | 42/42/0 | 42/42/0 | 94/94/0 | - |
| tou_window | 33/33/0 | 109/109/0 | 109/109/0 | 232/232/0 | - |
| tou_window_month | 396/396/0 | 1164/1164/0 | 1164/1164/0 | 2400/2400/0 | - |
| tariff_tou | 28/28/0 | 42/42/0 | 42/42/0 | 94/94/0 | - |
| demand_rule | 11/11/0 | 34/34/0 | 34/34/0 | 80/80/0 | - |
| tariff_demand_rule | 32/32/0 | 34/34/0 | 34/34/0 | 80/80/0 | - |
| eligibility_rule | 123/123/0 | 75/75/0 | 75/75/0 | 156/156/0 | - |
| tariff | - | - | - | - | 39/39/0 |
| tariff_listing | 56/56/0 | 70/70/0 | 104/104/0 | 112/112/0 | - |
| listing_flag | 88/88/0 | 74/74/0 | 200/200/0 | 164/164/0 | - |
| tariff_alias | - | 24/24/0 | 92/92/0 | 24/24/0 | - |
| tariff_relation | 1/1/0 | - | - | - | - |
| price_adjustment | 1/1/0 | 1/1/0 | 1/1/0 | 1/1/0 | - |
| price_adjustment_tariff | 18/18/0 | 34/34/0 | 34/34/0 | 34/34/0 | - |
| exception_instance | 111/111/0 | 117/117/0 | 94/94/0 | 116/116/0 | - |
| document_coverage | - | - | 5/5/0 | 5/5/0 | - |
| source_document | 2/2/0 | 2/1/0 (+1?) | 1/1/0 | 2/2/0 | - |

### energex
| table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 744/684/60 | 1063/1063/0 | 415/415/0 | 410/410/0 | - |
| metering_price | - | 1/1/0 | 15/15/0 | 16/16/0 | - |
| eligibility_rule | 118/118/0 | 156/156/0 | 51/51/0 | 51/51/0 | - |
| tariff | - | - | - | - | 50/50/0 |
| tariff_listing | 58/58/0 | 88/88/0 | 58/58/0 | 54/54/0 | - |
| listing_flag | 30/30/0 | 37/37/0 | 42/42/0 | 38/38/0 | - |
| tariff_alias | - | - | 29/29/0 | - | - |
| price_adjustment | - | - | 1/1/0 | 1/1/0 | - |
| price_adjustment_tariff | - | - | 13/13/0 | 13/13/0 | - |
| exception_instance | 28/28/0 | 50/50/0 | 44/44/0 | 39/39/0 | - |
| document_coverage | - | - | 3/3/0 | 5/5/0 | - |
| source_document | 2/2/0 | 3/2/0 (+1?) | 1/1/0 | 1/1/0 | - |

### ergon
| table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 6704/6704/0 | 9307/9307/0 | 1372/1372/0 | 1254/1254/0 | - |
| metering_price | - | 1/1/0 | 44/44/0 | 42/42/0 | - |
| charge_step | 12/12/0 | 12/12/0 | 2/2/0 | - | - |
| eligibility_rule | 802/802/0 | 951/951/0 | 172/172/0 | 144/144/0 | - |
| tariff | - | - | - | - | 247/247/0 |
| tariff_listing | 434/434/0 | 628/628/0 | 179/179/0 | 159/159/0 | - |
| listing_flag | 578/578/0 | 783/783/0 | 207/207/0 | 181/181/0 | - |
| tariff_alias | - | 205/205/0 | 81/81/0 | - | - |
| price_adjustment | - | - | 1/1/0 | 1/1/0 | - |
| price_adjustment_tariff | - | - | 36/36/0 | 39/39/0 | - |
| exception_instance | 152/152/0 | 424/424/0 | 130/130/0 | 87/87/0 | - |
| document_coverage | - | - | 3/3/0 | 5/5/0 | - |
| source_document | 2/2/0 | 3/2/0 (+1?) | 1/1/0 | 1/1/0 | - |

### powerwater
| table | 2023-24 | 2024-25 | 2025-26 | 2026-27 | all |
|---|---|---|---|---|---|
| charge | 51/51/0 | 94/85/0 (+9?) | 88/73/0 (+15?) | 44/44/0 | - |
| metering_price | - | - | 8/8/0 | 4/4/0 | - |
| tou_schedule | 4/4/0 | 6/6/0 | 6/6/0 | 3/3/0 | - |
| tou_window | 4/4/0 | 26/26/0 | 26/26/0 | 13/13/0 | - |
| tou_window_month | 36/36/0 | 168/168/0 | 168/168/0 | 84/84/0 | - |
| tariff_tou | 8/8/0 | 20/20/0 | 20/20/0 | 10/10/0 | - |
| demand_rule | 4/4/0 | 4/4/0 | 4/4/0 | 2/2/0 | - |
| tariff_demand_rule | 8/8/0 | 8/0/8 | 8/0/8 | 4/0/4 | - |
| eligibility_rule | 37/37/0 | 82/82/0 | 56/56/0 | 36/36/0 | - |
| tariff | - | - | - | - | 10/10/0 |
| tariff_listing | 14/14/0 | 24/24/0 | 32/32/0 | 16/16/0 | - |
| listing_flag | 7/7/0 | 8/8/0 | 8/8/0 | - | - |
| tariff_alias | - | - | 16/16/0 | - | - |
| tariff_relation | - | 4/4/0 | - | - | - |
| exception_instance | 5/5/0 | 9/9/0 | 6/6/0 | 6/6/0 | - |
| document_coverage | - | - | 5/5/0 | 5/5/0 | - |
| source_document | 2/2/0 | 3/2/0 (+1?) | 2/2/0 | 2/2/0 | - |

## Findings: DB wrong
| distributor | FY | table | rows | source location | DB value | source value | class |
|---|---|---|---|---|---|---|---|
| Ausgrid | 2023-24 | charge | 8 | ausgrid-2023-24-annual-scs-pricing-proposal-31mar2023 pdf:p12-15, Table 4.1, EA302 and EA316 | component_label "Demand charge - Peak", charge_type demand | column sits under the "Capacity charge" header (c/kW/day); values match | DB wrong |
| Ausgrid | 2024-25..2026-27 | charge | 3 | ausgrid-network-price-list-{2024-25,2025-26,2026-27} pdf:p1, EA302 row | "Network Demand Prices - Peak", charge_type demand (40.7528 / 45.3369 / 48.3397) | column header "Network Capacity Prices" | DB wrong |
| Energex | 2023-24 (2 docs) | charge | 60 | energex-2023-24 price list + Sch8 wayback, xlsx:SACS Business header row | "Band 1 Charge" ... "Band 5 Charge" | "Band1 Charge" ... "Band5 Charge" (no space) | DB wrong (cosmetic) |
| Evoenergy | 2023-24 | charge | 4 | evoenergy-2023-24-electricity-network-pricing-proposal-5may2023 pdf-ocr:p43, tariff 108 "Peak period maximum demand: low season" | unit_published c/KVA/day | c/kVA/day (OCR case error) | DB wrong |
| Power and Water | 2023-24..2025-26 | tariff_demand_rule | 20 | tariff_demand_rule rows for powerwater:TARIFF5/TARIFF6 | season "On Season" / "Off Season" | charge.season for the same components is high / low, so the season join fails | DB wrong |

## Findings: source ambiguous (DB asserts more than the source prints)
| distributor | FY | table | rows | source location | DB value | source |
|---|---|---|---|---|---|---|
| Essential | 2024-25..2026-27 | charge | 3 | essential-price-list-and-explanatory-notes-* pdf:p1, BLND3TO row | "Demand - Peak" 14.2365 / 15.1692 / 15.4853 | value printed in one merged cell spanning the Peak and Shoulder demand columns |
| Evoenergy | 2023-24 | charge | 18 | proposal pdf-ocr:p45 and statement pdf:p26 and p29, tariffs 123 and 124 "Net energy consumption charge" | unit_std c/kVA/day for an energy charge | printed "c/kVA/day" and "c/KkA/day" (source typo; the 2024-25 schedule prints c/kWh) |
| Power and Water | 2024-25, 2025-26 | charge | 24 | pwc-scs-tariffs-2025-26 pdf:p1 (15 rows); pwc-scs-tariffs-2024-25 pdf:p1 Tariff 3a-3c TOU (9 rows) | unit_published $/kWh or $/kVA/month | no unit printed for those columns (2025-26 prints only "$/NMI/day") |
| All 7 | 2024-25 | source_document | 7 | aer-stakeholder-report-*-2024-25; the status is set by scripts/tariffdb/build_support.py:264-265 by default | price_status approved | the file is titled "... Annual Pricing Proposal"; its data sheet labels tables "Proposed prices"; no quoted evidence. Contradicts docs/tariffdb.md:105 ("never asserted without a source") |

## Findings: printed in the source, missing from the DB (completeness)
| distributor | FY | cells / codes | source location | DB state |
|---|---|---|---|---|
| Ausgrid | 2024-25..2026-27 | 51 | price lists pdf:p1 and p3, column "Metering Service Charge c/day" (18 / 13 / 20 cells) | stored nowhere (no charge or metering_price row), e.g. EA010 7.3671 |
| Evoenergy | 2023-24 | 31 | statement pdf:p22-26 Table 2.6, Metering capital (13) and Metering non-capital (18) columns | not stored; NUoS includes them (DB note) |
| Evoenergy | 2024-25 | 13 | schedule pdf, Metering column (x 640-710, e.g. 15.500 c/day) and "Rate + metering" column | not stored |
| Evoenergy | 2025-26, 2026-27 (3 docs) | 39 | xlsx:Network tariffs column G "Metering charge" (13 per doc); column H "Rate + metering" (138 / 150 / 150 cells, derived sums) | not stored |
| Endeavour | 2024-25..2026-27 | 32 | price lists, column "Export Energy All Time c/kWh" (2024-25 p42: 23 cells; p39 / p37 / p37: 3 cells each), all 0.0000 printed in black | no all-time export charge exists in the DB; 19 cells are on listed tariffs |
| Endeavour | 2024-25..2026-27 | 13 | tariff codes NESN, NESG, GENR (p39 / p37 / p37) and NFT2, NFT3, NFT4, NFIT (2024-25 p42) | not in tariff_listing; rows print only 0.0000; footnote (1): NESN, NESG and GENR only measure generation exports |
| Essential | 2024-25..2026-27 | 5 | pdf:p1 codes BLNREX2 and BLNBEX1 (2024-25), BLNE0AU (all 3 years) | not in tariff_listing; rows print only "-" |
| Endeavour, Ausgrid | all PDF years | 0 | GST-inclusive tables: Endeavour 2024-25 p40 (Table 1b) and p43 (3b), 2025-26 p38 (1b); Ausgrid p2 and p4 of each price list | not stored, while PWC SCS 2023-24 GST-inclusive rows are stored (17, gst=incl): inconsistent policy |
| Energex, Ergon, AER | various | 390 | repeated printings (AER 2024-25 Tariff schedule 2 "DMO tariffs" 104; Energex SACS Business 9000/9100; Ergon EVCT/EVNT/MVCT/WVCT rows) | not stored, while 223 component_repeated_in_document exceptions ARE recorded elsewhere: inconsistent |

## Verifier misreads (re-checked, no DB error)
| first flag | resolution |
|---|---|
| Ergon 2024-25 Tariff Trials label "not in header" (192) | my header reader read the unit row; labels are in row 5 and match |
| Endeavour 2024-25 p39 export columns | tie-break between equal values misread; DB correct |
| Evoenergy statement: 12 code/label key mismatches | my column extractor merged rows; the row-aware re-extract (evo_row) matched 662/662 |
| PDF column assignment: 24 tokens off-centre (XOFF), 116 with no value (NOVALUE) | each re-checked: grey 0xf2f2f2 placeholder zeros, multi-word PWC codes and wide cells; no DB error |
| Cells "missing" from xlsx (2,595) | 1,723 other distributors' blocks, 459 column-index helper cells, 390 repeated printings (listed above), 23 Essential text cells: no further omission |
| Rounding (5 Ergon and Energex xlsx cells) | DB value equals the Excel display (15-significant-digit half-up rounding) |

## Interpretation notes (no mismatch counted)
- FY2023-24 has 366 days, but $/year is converted with /365 everywhere (documented convention) [UNSURE whether intended]
- Essential PSO-B (basic meter): the source applies "Summer Time" (last Sun Oct to last Sun Mar); the DB records time_basis local_time (DST dates differ by a few days)
- Power and Water "$/kVA" demand stored as $/kVA/month with period_inferred=1; the source prints no period [UNSURE]
- Energex and Ergon: no demand_rule or TOU windows, because no held source defines them (docs/tariffdb.md:206). The DB is correct; a source gap (Tariff Structure Statements not held)
- Evoenergy "Energy consumption" has a blank time_band where "Energy at any time" uses anytime (inconsistent vocabulary)
- Essential relation evidence quotes typos "BLNRSS1" and "BLNREX1"; the DB maps them to BLNBSS1 and BLNBEX1 with a sic note; the 2024-25 and 2025-26 PDFs confirm the mapping
- Ausgrid EA394 critical-peak 175644 / 179902.8185 / 186425.4804 c/kWh are printed in the source and the AER sheets (faithful; plausibility only) [UNSURE]

## Evidence
| claim | evidence |
|---|---|
| all charge values match | xlsx: 25,512/25,512 cells equal at Excel display precision; pdf: 2,670 clustered + 116 grey-zero/PWC + 24 off-centre all resolved; Evoenergy statement 662/662 and schedule 138/138 row-aware; OCR 376 = statement DUoS/TUoS (188) and the JS/NUoS sum check (94/94) |
| quotes at locators | 6,427/6,427 rule-table quotes found (exact, row-order or token-bag on the locator page / cell / row) |
| TOU tiling | 1,914 schedule x day x month combinations tile 24h exactly; tou_window_month 7,704 rows = expansion of tou_window.months |
| price_status | AER landing changelog (sources/aer/landing/*.html) rebuilt per version: 66/66 document_coverage rows and 600/600 proposed_price flags match |
| 2024-25 approved default | scripts/tariffdb/build_support.py:264-265 `d["price_status"] = "approved" if d["document_type"] == "aer_stakeholder_report"`; docs/tariffdb.md:105 rule |
| metering adders | endeavour 12.419125 x 100 / 365 = 3.4025; energex 44.1433 -> 12.0941; ergon 41.9443 -> 11.4916; essential 60.6088 -> 16.6052 c/day; every NUoS charge delta reproduced (LFiT 0.258 / 1.593 / 3.035 exact) |
| Endeavour All-Time export | PyMuPDF spans at x 775-805, colour 000000, header "Export Energy All Time c/kWh": 23 on 2024-25 p42 (NS70 ... NS39, NFT3, NFT4, NFIT, NFT2) + NESN/NESG/GENR on p39 / p37 / p37 |
| Ausgrid metering column | header "Metering Service Charge" at x~326; GST-exclusive pages 1 and 3: 18 / 13 / 20 cells (e.g. EA010 7.3671, EA029 3.3973*); 0 stored |
| Evoenergy metering column | xlsx:Network tariffs G "Metering charge" 13 non-zero per doc (16.77 / 18.36 / 18.36) and H "Rate + metering"; no DB cell in G or H |
| row-level list | mismatches.csv (243 rows: every DB-wrong and ambiguous row by id, plus every omitted cell or code; GST-incl tables and repeated printings as group rows) |

## Recommendations (work that should ship as a follow-up)
- Relabel the 11 Ausgrid capacity columns (charge_type capacity, label "Capacity charge - Peak" / "Network Capacity Prices"), as a new source-document version per the append-only rule
- Normalise Power and Water tariff_demand_rule.season to high/low (20 rows); add a test that every tariff_demand_rule.season exists in charge.season for the same tariff and document
- Fix the 4 Evoenergy OCR units (c/KVA/day -> c/kVA/day); add a unit-vocabulary test
- Set the 7 AER 2024-25 stakeholder reports to price_status unverified (or add a quoted evidence row), per docs/tariffdb.md:105
- Store the printed metering components: Ausgrid "Metering Service Charge" (51) and Evoenergy metering columns (statement 31, schedules 13 x 4), as metering_price or charge rows
- Store the Endeavour "Export Energy All Time" 0.0000 charges (32) and list the 18 unpriced printed codes as placeholder listings
- Pick one GST-inclusive policy (store all incl tables, or none, including PWC) and one repeated-printing policy (store or record exceptions consistently)
- Set unit_published NULL where no unit is printed (24 PWC rows) and keep the inference in unit_interpreted; flag the Evoenergy 123/124 c/kVA/day energy typo with an exception
- Optional: fetch the Energex and Ergon Tariff Structure Statements to fill the TOU and demand-rule gap

_Generated 2026-10-06 16:22 UTC; scratch scripts live in the session scratchpad and are not retained._
