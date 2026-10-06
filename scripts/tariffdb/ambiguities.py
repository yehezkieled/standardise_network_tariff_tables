"""Places where a source can be read more than one way (table exception_type: source_ambiguous).

Each entry quotes the source verbatim at its locator; the build re-reads the quote and records one exception_instance
per entry (per tariff when `codes` is given). The database keeps the reading named in `stored`; `alternative` names
the other reading the text allows. `finding` refers to the independent verification that raised it.
"""

AUSNET_APP_2324 = "sources/dnsp/ausnet/AusNet_Annual_Pricing_Proposal_2023-24.pdf"
AUSNET_APP_2425 = "sources/dnsp/ausnet/AusNet_Annual_Pricing_Proposal_2024-25.pdf"
AUSNET_2526 = "sources/dnsp/ausnet/AusNet_Network_Tariff_Schedule_2025-26.xlsx"
AUSNET_2627 = "sources/dnsp/ausnet/AusNet_Network_Tariff_Schedule_2026-27_20260515.xlsx"
TAS_GUIDE_2324 = "sources/aer/2023-24_price_lists/TasNetworks_2023-24_Network_tariff_application_and_price_guide_4Apr2023.pdf"
TAS_SCS_2324 = "sources/dnsp/tasnetworks/TasNetworks_Network_Tariff_Pricing_Schedule_SCS_2023-24.pdf"
TAS_SCS_2425 = "sources/dnsp/tasnetworks/TasNetworks_Network_Tariff_Pricing_Schedule_SCS_2024-25.pdf"
TAS_SCS_2526 = "sources/dnsp/tasnetworks/TasNetworks_Network_Tariff_Pricing_Schedule_SCS_2025-26.xlsx"
TAS_2627 = "sources/dnsp/tasnetworks/TasNetworks_Network_Pricing_Schedule_2026-27.xlsx"
EVO_STATEMENT_2324 = "sources/dnsp/evoenergy/Evoenergy_Statement_of_Tariff_Classes_and_Tariffs_2023-24.pdf"
EVO_PROPOSAL_2324 = "sources/aer/2023-24_price_lists/Evoenergy_2023-24_Electricity_network_pricing_proposal_5May2023.pdf"

S1_STORED = ("tou_schedule.time_basis = daylight_time: the windows are stated in 'ADST', kept as stated, with "
             "time_stated_in_daylight_time")
S1_ALT = ("AusNet's glossary defines 'Local time' as 'Daylight savings time in accordance with the Victorian Government's "
          "requirements' (annual pricing proposals 2023-24 p40, 2024-25 p33) and never defines 'ADST', so 'ADST' may "
          "mean local time (standard time while daylight saving is off)")
S3_STORED = "the Locational TUoS node charges are attached to tariff TAS15 only"
S3_ALT = ("the 2023-24 price guide p64 says they apply to TAS15 and to ITC (Individual Tariff Calculation) customers, "
          "who have no tariff code; the schedules print them as a separate table without naming the tariffs")
S4_STORED = "charge_type fixed (a per lamp-watt per day price, neither metered demand nor energy)"
S4_ALT = "demand: the sources label the column 'All demand', 'Demand (c/lamp watt/day)', 'All demand (Lamps)' or 'Lamps dmnd'"
BLND3TO_STORED = "'Demand - Peak' only (the AER and Essential spreadsheets give the same value as the peak demand price)"
BLND3TO_ALT = ("the row prints one demand value in a cell merged across the 'Peak' and 'Shoulder' demand columns, so it "
               "can be read as applying to shoulder demand too")
EVO_STORED = ("unit as printed, interpreted as c/kVA/day (unit_std c/kVA/day) for an energy consumption charge; "
              "'c/KkA/day' kept as printed")
EVO_ALT = ("the 2024-25 schedule prints the 'Net Energy' charge in c/kWh, so the 2023-24 'c/kVA/day' (and the 'c/KkA/day' "
           "typo) may stand for c/kWh")
PWC_STORED = ("unit_published NULL (the column prints no unit); unit_interpreted holds the assumed unit ($/kWh for "
              "energy, $/kVA/month for season demand), period inferred")
PWC_ALT = "any other unit: the price list prints no unit for these columns, so the unit is assumed, not published"
AER_2425_STORED = "source_document.price_status = unverified (price_status_unverified)"
AER_2425_ALT = ("approved: the 'Stakeholder report' sheet says 'This report is intended to compliment the AER's Statement "
                "of Reasons, published upon approval of the pricing proposal'; proposed: the data sheet heads its price "
                "tables 'Proposed prices'")

AMBIGUITIES = [
    # verifier B S1: AusNet 'ADST' windows
    dict(finding="verifier-B S1", distributor="ausnet", fin_year="2023-24", doc=AUSNET_APP_2324, locator="pdf:p40",
         quote="Local time Daylight savings time in accordance with the Victorian Government’s requirements",
         stored=S1_STORED, alternative=S1_ALT),
    dict(finding="verifier-B S1", distributor="ausnet", fin_year="2024-25", doc=AUSNET_APP_2425, locator="pdf:p33",
         quote="Local time Daylight savings time in accordance with the Victorian Government’s requirements",
         stored=S1_STORED, alternative=S1_ALT),
    dict(finding="verifier-B S1", distributor="ausnet", fin_year="2025-26", doc=AUSNET_2526,
         locator="xlsx:Tariff structures!E13", quote="3:00PM to 9:00PM ADST Monday to Friday",
         stored=S1_STORED, alternative=S1_ALT),
    dict(finding="verifier-B S1", distributor="ausnet", fin_year="2026-27", doc=AUSNET_2627,
         locator="xlsx:Tariff structures!E13", quote="3:00PM to 9:00PM ADST Monday to Friday",
         stored=S1_STORED, alternative=S1_ALT),
    # verifier B S2: transitional AusNet tariffs share a structure number but print no demand prices
    dict(finding="verifier-B S2", distributor="ausnet", fin_year="2026-27", doc=AUSNET_2627,
         locator="xlsx:Network 2026-27!E51", quote="Medium critical peak demand 160 MWh to 400 MWh (Transitional)",
         codes=["NAT56"],
         stored="NAT56 is linked to the capacity and critical-peak demand rules of its tariff structure (18)",
         alternative="its Capacity and Critical peak demand cells (U51, V51) are blank, so those demand rules may not "
                     "apply to it"),
    dict(finding="verifier-B S2", distributor="ausnet", fin_year="2026-27", doc=AUSNET_2627,
         locator="xlsx:Network 2026-27!E54", quote="Large critical peak demand 400 MWh to 750 MWh (Transitional)",
         codes=["NAT75"],
         stored="NAT75 is linked to the capacity and critical-peak demand rules of its tariff structure (13)",
         alternative="its Capacity and Critical peak demand cells (U54, V54) are blank, so those demand rules may not "
                     "apply to it"),
    # verifier B S3: TasNetworks Locational TUoS
    dict(finding="verifier-B S3", distributor="tasnetworks", fin_year="2023-24", doc=TAS_GUIDE_2324, locator="pdf:p64",
         quote="Locational TUoS charges for those customers supplied under network tariffs TAS15 – large business high "
               "voltage specified demand > 2MVA and ITC – Individual Tariff Calculation will apply",
         codes=["TAS15"], stored=S3_STORED, alternative=S3_ALT),
    dict(finding="verifier-B S3", distributor="tasnetworks", fin_year="2024-25", doc=TAS_SCS_2425, locator="pdf:p4",
         quote="Locational TUoS charges for 2024-25", codes=["TAS15"], stored=S3_STORED, alternative=S3_ALT),
    dict(finding="verifier-B S3", distributor="tasnetworks", fin_year="2025-26", doc=TAS_SCS_2526,
         locator="xlsx:Locational TUoS 2025-26!B1", quote="Locational TUoS charges for 2025-26", codes=["TAS15"],
         stored=S3_STORED, alternative=S3_ALT),
    dict(finding="verifier-B S3", distributor="tasnetworks", fin_year="2026-27", doc=TAS_2627,
         locator="xlsx:Locational TUoS 2026-27!B1", quote="Locational TUoS charges for 2026-27", codes=["TAS15"],
         stored=S3_STORED, alternative=S3_ALT),
    # verifier B S4: TASUMSSL c/lamp watt/day typed fixed
    dict(finding="verifier-B S4", distributor="tasnetworks", fin_year="2023-24", doc=TAS_GUIDE_2324, locator="pdf:p59",
         quote="All demand 0.091 0.025 0.116", codes=["TASUMSSL"], stored=S4_STORED, alternative=S4_ALT),
    dict(finding="verifier-B S4", distributor="tasnetworks", fin_year="2023-24", doc=TAS_SCS_2324, locator="pdf:p8",
         quote="Public lighting is charged on the basis of c/lamp watt/day", codes=["TASUMSSL"], stored=S4_STORED,
         alternative=S4_ALT),
    dict(finding="verifier-B S4", distributor="tasnetworks", fin_year="2024-25", doc=TAS_SCS_2425, locator="pdf:p1",
         quote="Street lighting TASUMSSL 0.132", codes=["TASUMSSL"], stored=S4_STORED, alternative=S4_ALT),
    dict(finding="verifier-B S4", distributor="tasnetworks", fin_year="2025-26", doc=TAS_SCS_2526,
         locator="xlsx:Network tariffs 2025-26!R4", quote="Demand (c/lamp watt/day)", codes=["TASUMSSL"],
         stored=S4_STORED, alternative=S4_ALT),
    dict(finding="verifier-B S4", distributor="tasnetworks", fin_year="2026-27", doc=TAS_2627,
         locator="xlsx:Network tariffs 2026-27!R4", quote="Demand (c/lamp watt/day)", codes=["TASUMSSL"],
         stored=S4_STORED, alternative=S4_ALT),
    dict(finding="verifier-B S4", distributor="tasnetworks", fin_year="2024-25",
         doc="sources/aer/2024-25_stakeholder_reports/AER_Stakeholder_report_TasNetworks_2024-25.xlsx",
         locator="xlsx:Tariff schedule!V16", quote="Lamps dmnd", codes=["TASUMSSL"], stored=S4_STORED,
         alternative=S4_ALT),
    dict(finding="verifier-B S4", distributor="tasnetworks", fin_year="2025-26",
         doc="sources/aer/AER_Consolidated_stakeholder_report_2025-26_v1_wayback.xlsx",
         locator="xlsx:Tariff schedule!T1098", quote="All demand (Lamps)", codes=["TASUMSSL"], stored=S4_STORED,
         alternative=S4_ALT),
    dict(finding="verifier-B S4", distributor="tasnetworks", fin_year="2025-26",
         doc="sources/aer/AER_Consolidated_stakeholder_report_2025-26_v5.xlsx",
         locator="xlsx:Tariff schedule!U1098", quote="All demand (Lamps)", codes=["TASUMSSL"], stored=S4_STORED,
         alternative=S4_ALT),
    dict(finding="verifier-B S4", distributor="tasnetworks", fin_year="2026-27",
         doc="sources/aer/AER_Consolidated_stakeholder_report_2026-27_26Aug2026.xlsx",
         locator="xlsx:Tariff schedule!U1098", quote="All demand (Lamps)", codes=["TASUMSSL"], stored=S4_STORED,
         alternative=S4_ALT),
    # verifier B S5: United Energy KEVC trial figure
    dict(finding="verifier-B S5", distributor="unitedenergy", fin_year="2026-27",
         doc="sources/dnsp/unitedenergy/UE_Pricing_Proposal_2026-27_07May2026.pdf", locator="pdf:p14",
         quote="Figure 4 KEVC trial network tariff structure",
         stored="windows read from the figure up to 9pm; the unlabelled 9pm-12am segment is not encoded and no tariff "
                "is linked (the PDF prints no code for the trial)",
         alternative="the 9pm-12am segment is shaded like off-peak, and the 2026-27 tariff summary names the trial "
                     "EVUTOU, so the schedule may extend to midnight and belong to EVUTOU"),
    # verifier A: Essential BLND3TO merged demand cell
    dict(finding="verifier-A ambiguous", distributor="essential", fin_year="2024-25",
         doc="sources/dnsp/essential/Essential_Price_List_and_Explanatory_Notes_2024-25.pdf", locator="pdf:p1",
         quote="BLND3TO LV Demand - Alternative tariff PSO-Int", codes=["BLND3TO"],
         stored=BLND3TO_STORED + ": 14.2365", alternative=BLND3TO_ALT),
    dict(finding="verifier-A ambiguous", distributor="essential", fin_year="2025-26",
         doc="sources/dnsp/essential/Essential_Price_List_and_Explanatory_Notes_2025-26.pdf", locator="pdf:p1",
         quote="BLND3TO LV Demand - Alternative tariff PSO-Int", codes=["BLND3TO"],
         stored=BLND3TO_STORED + ": 15.1692", alternative=BLND3TO_ALT),
    dict(finding="verifier-A ambiguous", distributor="essential", fin_year="2026-27",
         doc="sources/dnsp/essential/Essential_Price_List_and_Explanatory_Notes_2026-27.pdf", locator="pdf:p1",
         quote="BLND3TO LV Demand - Alternative tariff PSO-Int", codes=["BLND3TO"],
         stored=BLND3TO_STORED + ": 15.4853", alternative=BLND3TO_ALT),
    # verifier A: Evoenergy 123/124 energy charge printed per kVA per day
    dict(finding="verifier-A ambiguous", distributor="evoenergy", fin_year="2023-24", doc=EVO_STATEMENT_2324,
         locator="pdf:p26", quote="Net energy c/kVA/day 0.000 0.000 0.500 0.500", codes=["123"],
         stored=EVO_STORED + " (also p29)", alternative=EVO_ALT),
    dict(finding="verifier-A ambiguous", distributor="evoenergy", fin_year="2023-24", doc=EVO_STATEMENT_2324,
         locator="pdf:p26", quote="Net energy c/KkA/day 0.000 0.000 0.500 0.500", codes=["124"],
         stored=EVO_STORED + " (also p29)", alternative=EVO_ALT),
    dict(finding="verifier-A ambiguous", distributor="evoenergy", fin_year="2023-24", doc=EVO_PROPOSAL_2324,
         locator="pdf-ocr:p45", quote="Netenergy c/kVA/day 595,087", codes=["123"], stored=EVO_STORED,
         alternative=EVO_ALT),
    dict(finding="verifier-A ambiguous", distributor="evoenergy", fin_year="2023-24", doc=EVO_PROPOSAL_2324,
         locator="pdf-ocr:p45", quote="Net energy c/KkA/day 0 0.000", codes=["124"], stored=EVO_STORED,
         alternative=EVO_ALT),
    # verifier A: Power and Water columns with no printed unit
    dict(finding="verifier-A ambiguous", distributor="powerwater", fin_year="2024-25",
         doc="sources/dnsp/powerwater/PWC_SCS_Tariffs_2024-25_wayback.pdf", locator="pdf:p1",
         quote="(Low Period) (Mid Period) (High Period)", stored=PWC_STORED + " for the Low/Mid/High Period columns",
         alternative=PWC_ALT),
    dict(finding="verifier-A ambiguous", distributor="powerwater", fin_year="2025-26",
         doc="sources/dnsp/powerwater/PWC_SCS_Tariffs_2025-26_210525_wayback.pdf", locator="pdf:p1",
         quote="2025-26 SCS tariffs by charging parameter (excluding GST)",
         stored=PWC_STORED + " for every column but SAC ('$/NMI/day')", alternative=PWC_ALT),
    # verifier A: AER 2024-25 per-distributor reports - proposed or approved prices
    dict(finding="verifier-A ambiguous", distributor="sapn", fin_year="2024-25",
         doc="sources/aer/2024-25_stakeholder_reports/AER_Stakeholder_report_SAPN_2024-25_updated17Jul2024.xlsx",
         locator="xlsx:Stakeholder report!B7",
         quote="This report is intended to compliment the AER's Statement of Reasons, published upon approval of the "
               "pricing proposal",
         stored=AER_2425_STORED,
         alternative="approved, per this sentence; this report has no data sheet, but the other 2024-25 reports head "
                     "their price tables 'Proposed prices'"),
] + [
    dict(finding="verifier-A ambiguous", distributor=did, fin_year="2024-25",
         doc=f"sources/aer/2024-25_stakeholder_reports/AER_Stakeholder_report_{name}_2024-25.xlsx",
         locator="xlsx:Stakeholder report data!B874", quote="Supporting table 11 | Proposed prices - residential",
         stored=AER_2425_STORED, alternative=AER_2425_ALT)
    for did, name in [("ausgrid", "Ausgrid"), ("ausnet", "AusNet"), ("citipower", "CitiPower"),
                      ("endeavour", "Endeavour"), ("energex", "Energex"), ("ergon", "Ergon"),
                      ("essential", "Essential"), ("evoenergy", "Evoenergy"), ("jemena", "Jemena"),
                      ("powercor", "Powercor"), ("powerwater", "PWC"), ("tasnetworks", "TasNetworks"),
                      ("unitedenergy", "UnitedEnergy")]
]
