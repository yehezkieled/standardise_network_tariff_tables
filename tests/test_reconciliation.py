import builtins
import csv
import os
import re
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import openpyxl
import reconcile
import parse_aer
from published import cell_value
from schema import COLUMNS
from units import to_std
from dnsp import essential_evoenergy, ausgrid_endeavour, cp_pc_ue, tasnetworks, sapn_pwc, energex_ergon
import fetch_sources


# The TasNetworks checks parse the published documents themselves. Those files are not committed (./run.sh fetches
# them from the URLs in sources/inventory.csv), so the checks run only in a checkout that has them.
TASNETWORKS_DOCUMENTS = [path for _, _, path, _ in tasnetworks.FILES[2:]]
needs_tasnetworks_documents = unittest.skipUnless(
    all(os.path.exists(path) for path in TASNETWORKS_DOCUMENTS),
    'TasNetworks source documents are not in the checkout; run ./run.sh to fetch them')


def row(side, value, band='peak', unit='c/kWh', charge='energy', season='', code='TEST', note='', distributor='Evoenergy'):
    result = dict.fromkeys(COLUMNS, '')
    std, std_unit = to_std(value, unit)
    result.update(side=side, distributor=distributor, fin_year='2025-26', tariff_code=code,
                  tariff_name=code, component=f'{season} {band} {charge}'.strip(), charge_type=charge,
                  time_band=band, season=season, unit=unit, value=value, value_std=std,
                  unit_std=std_unit, gst='excl', basis='NUoS', source_file=side+'.pdf', note=note)
    return result


class ReconciliationRegression(unittest.TestCase):
    def test_tariff_name_matching_preserves_numeric_identity(self):
        for acode, dcode in [('LBAD627', 'LBADCBD'), ('LBADCBD', 'LBAD627'),
                             ('LBAD627', 'OTHER628')]:
            for placeholder in [False, True]:
                a = row('AER', '0' if placeholder else '1', code=acode, distributor='SA Power Networks')
                d = row('DNSP', '1', code=dcode, distributor='SA Power Networks')
                a['tariff_name'] = d['tariff_name'] = 'Large LV Business Annual Demand'
                if placeholder:
                    a['component'] = '(no non-zero prices)'
                detail, grid = self.compare([a, d])
                self.assertIsNone(grid)
                self.assertEqual({r['status'] for r in detail}, {'aer_only_code', 'dnsp_only_code'})
                self.assertEqual({r['tariff_code'] for r in detail}, {acode, dcode})
                if placeholder:
                    self.assertEqual(next(r for r in detail if r['status'] == 'aer_only_code')['explanation'],
                                     'aer_zero_placeholder')
        for acode, dcode in [('LBAD627', 'OTHER627'), ('LBAD', 'OTHER')]:
            for codes in [(acode, dcode), (dcode, acode)]:
                a = row('AER', '1', code=codes[0], distributor='SA Power Networks')
                d = row('DNSP', '1', code=codes[1], distributor='SA Power Networks')
                a['tariff_name'] = d['tariff_name'] = 'Large LV Business Annual Demand'
                detail, grid = self.compare([a, d])
                self.assertEqual(grid['components_compared'], 1)
                self.assertEqual(grid['codes_mapped_by_name'], 1)
                self.assertEqual(detail[0]['status'], 'equal')
                self.assertIn('matched by tariff name', detail[0]['detail'])

    def test_sapn_published_variant_codes(self):
        def word(text, centre):
            return SimpleNamespace(text=text, x0=centre - 2, x1=centre + 2, xc=centre)

        header = SimpleNamespace(body_start=0, left_edge=150, code_cols=[(10, 'SA'), (40, 'CBD'), (70, 'EXPORT')],
                                 centres=[200, 250, 300, 350, 400], pitch=50,
                                 groups=['SUPPLY', 'ENERGY BASED USAGE', 'EXPORT', 'EXPORT', 'METERING'],
                                 units=['$/day', '$/kWh', '$/kWh', '$/kWh', '$/day'], elig=[''] * 5,
                                 component=lambda k, residential: ['Supply Rate', 'Peak energy', 'Export Charge',
                                                                  'Export Credit', 'Meter Charge'][k])
        for side in ['DNSP', 'AER_HOSTED']:
            for basis in ['NUoS', 'DUoS', 'TUoS', 'JSO']:
                ctx = dict(fin_year='2025-26', side=side, file='fixture.pdf', url='', page=1,
                           table_note='schedule', gst_note='excl')
                for parent, cbd, export in [('LBAD', 'LBADCBD', '-'), ('RSR', 'RSR', 'RSRNE'),
                                            ('TEST', 'TESTCBD', 'TESTNE')]:
                    words = [word(parent, 10), word(cbd, 40), word(export, 70), word('Business', 100)]
                    words += [word(value, centre) for value, centre in zip(
                        ['$1.00', '$0.20', '$0.01', '-$0.10', '$0.05'], header.centres)]
                    line = SimpleNamespace(words=words, bold=False, text=' '.join(w.text for w in words))
                    rows = sapn_pwc.sapn_parse_page([line], header, basis, ctx, {'stack': ['Small Business']})
                    codes = {parent, cbd, export} - {'-'}
                    self.assertEqual({r['tariff_code'] for r in rows}, codes)
                    for code in codes:
                        components = {r['component']: r['value'] for r in rows if r['tariff_code'] == code}
                        expected = {'Supply Rate': '1.00', 'Peak energy': '0.20'}
                        if code != export:
                            expected.update({'Export Charge': '0.01', 'Export Credit': '-0.10'})
                        self.assertEqual(components, expected)
                        self.assertEqual(sum(r['tariff_code'] == code for r in rows), len(expected))
                    if side == 'DNSP' and basis == 'NUoS':
                        aer = [dict(r, side='AER') for r in rows if r['tariff_code'] == parent]
                        detail, grid = self.compare(aer + rows)
                        self.assertEqual(grid['components_compared'], 4)
                        variants = [r for r in detail if r['status'] == 'dnsp_only_code']
                        self.assertEqual({r['tariff_code'] for r in variants}, codes - {parent})
                        if cbd != parent:
                            self.assertEqual(next(r for r in variants if r['tariff_code'] == cbd)['explanation'],
                                             'site_specific_variant')
                        detail, grid = self.compare([dict(r, side='AER') for r in rows] + rows)
                        self.assertEqual(grid['components_compared'], len(rows))
                        self.assertTrue(all(r['status'] == 'equal' for r in detail))

    def test_ergon_minimum_and_remaining_capacity(self):
        for code in ['EBPMPT1', 'EBPMPT2', 'EBPMPT3']:
            sheet = energex_ergon.Sheet()
            aer = []
            for index, (alabel, dlabel, value, band) in enumerate([
                    ('Min. Cap.', 'Minimum Capacity Charge', '3.8160', 'capacity_minimum'),
                    ('Rem. Cap.', 'Remaining Capacity Charge', '11.5210', 'capacity_remaining')]):
                sheet.records.append(dict(basis='NUoS', code=code, name=code, zone='', cls='',
                                          comp=dlabel, unit='$/kW', value=float(value), published=value, row=index + 1))
                a = parse_aer.row('Ergon Energy', '2024-25', code, '', '', alabel,
                                  '$/kW', value, 'NUoS', 'aer.xlsx', '')
                self.assertEqual((a['charge_type'], a['time_band']), ('capacity', band))
                aer.append(a)
            dnsp = energex_ergon.rows_for_sheet(sheet, 'Business', 'Ergon Energy', '2024-25',
                                               'DNSP', 'fixture.xlsx', '')
            dnsp = [{key: r[key] for key in COLUMNS} for r in dnsp]
            self.assertTrue(all(r['charge_type'] == 'capacity' for r in dnsp))
            for pair in [aer + dnsp, [dict(r, side='DNSP' if r['side'] == 'AER' else 'AER')
                                     for r in aer + dnsp]]:
                detail, grid = self.compare(pair)
                self.assertEqual(grid['components_compared'], 2)
                self.assertTrue(all(r['status'] == 'equal' for r in detail))
            swapped = [dict(dnsp[0], value=dnsp[1]['value'], value_std=dnsp[1]['value_std']),
                       dict(dnsp[1], value=dnsp[0]['value'], value_std=dnsp[0]['value_std'])]
            detail, grid = self.compare(aer + swapped)
            self.assertEqual(grid['components_compared'], 2)
            self.assertEqual([r['status'] for r in detail], ['value_differs', 'value_differs'])
            for a, d in [(aer[0], dnsp[1]), (aer[1], dnsp[0])]:
                detail, grid = self.compare([a, dict(d, value=a['value'], value_std=a['value_std'])])
                self.assertIsNone(grid)
        a = parse_aer.row('Ergon Energy', '2024-25', 'EC22BTOU', '', '', 'Non-Sum. Cap.',
                          '$/kVA', '1.0000', 'NUoS', 'aer.xlsx', '')
        self.assertEqual(a['charge_type'], 'capacity')

    def test_ergon_threshold_peak_blocks(self):
        for code in ['EBFRMT1', 'EBFRMT2', 'EBFRMT3', 'EBIRRT1', 'EBIRRT2', 'EBIRRT3']:
            sheet = energex_ergon.Sheet()
            aer = []
            blocks = [(1, 'Volume Peak Charge', '0.4758', '0.47584')]
            if code.startswith('EBFRMT'):
                blocks.append((2, 'Volume Peak Charge (over 10,000)', '0.3789', '0.37886'))
            for block, label, av, dv in blocks:
                sheet.records.append(dict(basis='NUoS', code=code, name=code, zone='', cls='',
                                          comp=label, unit='$/kWh', value=float(dv), published=dv, row=block))
                aer.append(parse_aer.row('Ergon Energy', '2024-25', code, '', '', f'Pk Block {block}',
                                         '$/kWh', av, 'NUoS', 'aer.xlsx', ''))
            dnsp = energex_ergon.rows_for_sheet(sheet, 'Business', 'Ergon Energy', '2024-25',
                                               'DNSP', 'fixture.xlsx', '')
            dnsp = [{key: r[key] for key in COLUMNS} for r in dnsp]
            self.assertEqual([r['time_band'] for r in aer], [r['time_band'] for r in dnsp])
            detail, grid = self.compare(aer + dnsp)
            self.assertEqual(grid['components_compared'], len(blocks))
            self.assertTrue(all(r['status'] == 'equal_after_rounding' for r in detail))
            if len(blocks) == 2:
                detail, grid = self.compare([aer[0], dnsp[1]])
                self.assertIsNone(grid)

    def test_sapn_summer_abbreviations(self):
        for code in ['BD', 'HBD', 'SBD']:
            for abbreviation in ['Smmr', 'Smr', 'Sum.']:
                a = parse_aer.row('SA Power Networks', '2024-25', code, '', '', f'Mth Dmnd {abbreviation}',
                                  '$/kVA', '0.3962', 'NUoS', 'aer.xlsx', '')
                d = sapn_pwc.make_row('SA Power Networks', '2024-25', 'DNSP', code, '', '',
                                     f'MONTHLY kVA DEMAND - Actual Monthly Demand - {code} Summer 5',
                                     '$/kVA/day', '0.3962', 'excl', 'NUoS', 'fixture.pdf', '', [])
                self.assertEqual((a['season'], d['season']), ('summer', 'summer'))
                for pair in ([a, d], [dict(a, season=''), dict(d, season='')]):
                    detail, grid = self.compare(pair)
                    self.assertEqual(grid['components_compared'], 1)
                    self.assertEqual(detail[0]['status'], 'equal')

    def compare(self, rows):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / 'out/dnsp').mkdir(parents=True)
            (root / 'sources').mkdir()
            (root / 'sources/inventory.csv').write_text('local_path\n')
            for filename, side in [('out/aer_long.csv', 'AER'), ('out/dnsp/test.csv', 'DNSP')]:
                with (root / filename).open('w', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=COLUMNS)
                    writer.writeheader()
                    writer.writerows(r for r in rows if r['side'] == side)
            with patch.object(reconcile, 'ROOT', str(root)):
                detail, grid, _ = reconcile.reconcile()
            return detail, next(g for g in grid if g['components_compared']) if any(g['components_compared'] for g in grid) else None

    def test_remaining_identity_aliases(self):
        cases = [('Block 3', 'Block3', 'c/kWh'), ('Volume', 'Volume Charge', 'c/kWh'),
                 ('Net energy consumption', 'Net Energy', 'c/kWh'),
                 ('App. Capacity', 'Peak capacity', 'c/kVA/day'),
                 ('Ann Dmnd Pk', 'Peak demand', 'c/kW/day'),
                 ('Opk energy', 'Off-peak energy', 'c/kWh'),
                 ('Peak Sum. demand', 'Peak summer demand', 'c/kW/day'),
                 ('Peak Non-Sum. demand', 'Peak non-summer demand', 'c/kW/day'),
                 ('Ann Dmnd', 'Anytime demand', 'c/kW/day'),
                 ('DER export', 'Export - Rebate', 'c/kWh', '-4.8793'),
                 ('Non-Summ.', 'Volume Non Summer Charge', 'c/kWh'),
                 ('On Demand', 'On Season Demand Charge', '$/kVA/month', '15.0000'),
                 ('Off Demand', 'Off Season Demand Charge', '$/kVA/month', '2.2000'),
                 ('HS Peak exp', 'Export - Energy Charges - High Season Peak', 'c/kWh', '-11.0357'),
                 ('LS Peak exp', 'Export - Energy Charges - Low Season Peak', 'c/kWh', '-3.2695'),
                 ('SS Pk blk 2 exp', 'Export - Energy Charges - Solar Soak Period Block 2', 'c/kWh', '1.7500'),
                 ('12-month rolling demand', 'Demand Charges - Rolling peak', 'c/kW/month'),
                 ('Demand kVA', 'Peak kVA Demand', 'c/kVA/month'),
                 ('Export Credit', 'EXPORT - Export Credit - Peak', 'c/kWh', '-1.0000'),
                 ('Peak Shoulder Import Mar-May, Sep-Nov', 'Usage Charges - Peak import Mar-May, Sep-Nov', 'c/kWh')]
        for alabel, dlabel, unit, *given in cases:
            with self.subTest(label=alabel):
                value = given[0] if given else '1.0000'
                a = parse_aer.row('Ausgrid', '2025-26', 'TEST', '', '', alabel, unit, value, 'NUoS', 'aer.xlsx', '')
                d = ausgrid_endeavour.make_row('Ausgrid', '2025-26', 'DNSP', 'dnsp.pdf', '', 'TEST', '', '',
                                             dlabel, unit, value, 'NUoS', 'excl', '')
                for pair in ([a, d], [dict(d, side='AER'), dict(a, side='DNSP')]):
                    detail, grid = self.compare(pair)
                    self.assertEqual(grid['components_compared'], 1)
                    self.assertEqual(detail[0]['status'], 'equal')

    @needs_tasnetworks_documents
    def test_tasnetworks_tariff_measures(self):
        for year, side, path, parser in tasnetworks.FILES[2:]:
            rows = parser(path, year, side, '')
            for code, measure in tasnetworks.DEMAND_MEASURE_2023_24.items():
                demand = [r for r in rows if r['tariff_code'] == code and '/k' in r['unit'] and 'Wh' not in r['unit']]
                self.assertTrue(demand, (year, code))
                self.assertTrue(all('/'+measure+'/' in r['unit'] for r in demand), (year, code))

    @needs_tasnetworks_documents
    def test_tasnetworks_unestablished_measures_follow_header(self):
        for year, side, path, parser in tasnetworks.FILES[2:]:
            rows = parser(path, year, side, '')
            demand = [r for r in rows if re.fullmatch(r'TAS(?:84T[1-4]|14T[12])', r['tariff_code'])
                      and '/k' in r['unit'] and 'Wh' not in r['unit']]
            self.assertTrue(demand, year)
            for r in demand:
                if year == '2024-25':
                    self.assertTrue(r['unit_std'].startswith('c/kVA/'), (year, r['tariff_code'], r['unit_std']))
                    self.assertNotIn('[UNSURE]', r['note'])
                else:
                    self.assertTrue(r['unit_std'].startswith('c/k?/'), (year, r['tariff_code'], r['unit_std']))
                    self.assertIn('[UNSURE]', r['note'])

    def test_seasonal_unit_suffix_is_daily_price_in_that_season(self):
        for alabel, unit, dlabel, season in [('HS dem kVA', 'cents/kVA/highsn', 'Import - Demand Charges - High Season Demand', 'high'),
                                             ('Demand kVA low', 'cents/kVA/lowsn', 'Low Season Demand', 'low'),
                                             ('SDIC', 'cents/kVA/Summer', 'Summer Demand Incentive Charge', 'summer')]:
            with self.subTest(unit=unit):
                a = parse_aer.row('Endeavour Energy', '2025-26', 'TEST', '', '', alabel, unit, '42.7800', 'NUoS', 'aer.xlsx', '')
                self.assertEqual((a['unit_std'], a['season']), ('c/kVA/day', season))
                d = ausgrid_endeavour.make_row('Endeavour Energy', '2025-26', 'DNSP', 'dnsp.pdf', '', 'TEST', '', '',
                                             dlabel, 'c/kVA/day', '42.7800', 'NUoS', 'excl', '')
                detail, grid = self.compare([a, d])
                self.assertEqual(grid['components_compared'], 1)
                self.assertEqual(detail[0]['status'], 'equal')

    def test_unknown_demand_quantity_compares_as_unit_unverified(self):
        a = row('AER', '32.9750', band='peak', unit='c/kVA/day', charge='demand')
        d = row('DNSP', '32.975', band='peak', unit='c/kVA or kW/day', charge='demand')
        for pair in ([a, d], [dict(d, side='AER'), dict(a, side='DNSP')]):
            detail, grid = self.compare(pair)
            self.assertEqual(grid['components_compared'], 1)
            self.assertEqual(detail[0]['status'], 'equal')
            self.assertIn('unit unverified', detail[0]['detail'])
        detail, grid = self.compare([a, row('DNSP', '32.975', band='peak', unit='c/kW/day', charge='demand')])
        self.assertIsNone(grid)

    def test_unique_compatible_labels_match(self):
        a = row('AER', '52.1300', band='', unit='c/day', charge='fixed', code='N70', distributor='Endeavour Energy')
        d = row('DNSP', '55.5325', band='', unit='c/day', charge='fixed', code='N70', distributor='Endeavour Energy')
        a['component'], d['component'] = 'Fixed', 'Daily Access Charge'
        for aer, dnsp in [(a, d), (dict(a, value='55.5325', value_std=55.5325),
                                  dict(d, value='52.1300', value_std=52.1300))]:
            detail, grid = self.compare([aer, dnsp])
            self.assertEqual(len(detail), 1)
            self.assertEqual(detail[0]['status'], 'value_differs')
            self.assertEqual(grid['unexplained'], 1)
            self.assertEqual(grid['components_compared'], 1)

    def test_swapped_export_directions_remain_discrepancies(self):
        a = parse_aer.row('Ausgrid', '2025-26', 'EA029', '', '', 'Energy (charge)', 'c/kWh',
                          '1.2029', 'NUoS', 'aer.xlsx', '')
        b = parse_aer.row('Ausgrid', '2025-26', 'EA029', '', '', 'Energy (reward)', 'c/kWh',
                          '-2.3951', 'NUoS', 'aer.xlsx', '')
        d = ausgrid_endeavour.make_row('Ausgrid', '2025-26', 'DNSP', 'dnsp.pdf', '', 'EA029', '', '',
                                      'Opt in export charge', 'c/kWh', '-2.3951', 'NUoS', 'excl', '')
        e = ausgrid_endeavour.make_row('Ausgrid', '2025-26', 'DNSP', 'dnsp.pdf', '', 'EA029', '', '',
                                      'Opt in export reward', 'c/kWh', '1.2029', 'NUoS', 'excl', '')
        detail, grid = self.compare([a, b, d, e])
        self.assertEqual(grid['unexplained'], 2)
        self.assertEqual({r['status'] for r in detail}, {'value_differs'})
        self.assertTrue(all(('reward' in r['component']) == ('reward' in r['dnsp_component']) for r in detail))

    def test_ambiguous_labels_are_not_guessed(self):
        a = row('AER', '1.000', band='', unit='c/day', charge='fixed')
        b = dict(a, component='def', value='2.000', value_std=2)
        a['component'] = 'abc'
        d = row('DNSP', '3.000', band='', unit='c/day', charge='fixed')
        d['component'] = 'xyz'
        detail, grid = self.compare([a, b, d])
        self.assertIsNone(grid)
        self.assertEqual(Counter(r['status'] for r in detail), {'aer_only_component': 2, 'dnsp_only_component': 1})

    def test_established_component_identities_match(self):
        cases = [
            ('Ausgrid', 'EA335', 'Critical minimum energy', 'Network Energy Prices - Critical minimum energy', 'c/kWh', '38.0000'),
            ('Ausgrid', 'EA335', 'Critical peak energy', 'Network Energy Prices - Critical peak energy', 'c/kWh', '-86.0000'),
            ('Ausgrid', 'EA974', 'Dynamic minimum energy', 'Network Energy Prices - Dynamic (minimum)', 'c/kWh', '1.0000'),
            ('Ausgrid', 'EA974', 'Dynamic maximum energy', 'Network Energy Prices - Dynamic (maximum)', 'c/kWh', '2.0000'),
            ('Ausgrid', 'EA029', 'Energy (charge)', 'Network Energy Prices - Opt in export charge', 'c/kWh', '1.2029'),
            ('Ausgrid', 'EA029', 'Energy (reward)', 'Network Energy Prices - Opt in export reward', 'c/kWh', '-2.3951'),
            ('Ausgrid', 'EA302', 'Real Capacity', 'Network Demand Prices - Peak', 'c/kW/day', '40.7528'),
            ('CitiPower', 'SUMMER', 'Peak capacity Dec-Mar', 'Capacity charge - Peak summer', 'c/kVA/month', '1.0000'),
            ('Powercor', 'NONSUMMER', 'Peak capacity Apr-Nov', 'Capacity charge - Peak non-summer', 'c/kVA/month', '1.0000'),
            ('CitiPower', 'CRSTOU', 'Saver energy', 'Usage Charges - Saver', 'c/kWh', '1.0000'),
            ('CitiPower', 'CRCER', 'Saver Export Sep - May', 'Usage Charges - Saver Export Sep-May', 'c/kWh', '-1.0000'),
            ('Powercor', 'PRSTOU', 'Saver energy', 'Usage Charges - Saver', 'c/kWh', '1.0000'),
            ('United Energy', 'URSTOU', 'Saver energy', 'Usage Charges - Saver', 'c/kWh', '1.0000'),
        ]
        for dist, code, alabel, dlabel, unit, value in cases:
            for year in ('2024-25', '2025-26', '2026-27'):
                with self.subTest(dist=dist, code=code, label=alabel, year=year):
                    a = parse_aer.row(dist, year, code, '', '', alabel, unit, value, 'NUoS', 'aer.xlsx', '')
                    if dist == 'Ausgrid':
                        d = ausgrid_endeavour.make_row(dist, year, 'DNSP', 'dnsp.pdf', '', code, '', '', dlabel, unit, value, 'NUoS', 'excl', '')
                    else:
                        d = cp_pc_ue.make_row(dist=dist, fin_year=year, code=code, name='', component=dlabel,
                                             unit=unit, value=value, gst='excl', basis='NUoS',
                                             source_file='sources/dnsp/fixture.xlsx', url='', note='')
                    detail, grid = self.compare([a, d])
                    self.assertEqual(len(detail), 1)
                    self.assertEqual(detail[0]['status'], 'equal')
                    self.assertEqual(grid['components_compared'], 1)

    def test_normalized_bands_still_reject_conflicts(self):
        for alabel, dlabel in [('Critical minimum energy', 'Critical peak energy'),
                               ('Dynamic minimum energy', 'Dynamic maximum energy'),
                               ('Saver energy', 'Peak energy')]:
            for value in ['1.0000', '2.0000']:
                a = parse_aer.row('Ausgrid', '2025-26', 'EA974', '', '', alabel, 'c/kWh', '1.0000', 'NUoS', 'aer.xlsx', '')
                d = ausgrid_endeavour.make_row('Ausgrid', '2025-26', 'DNSP', 'dnsp.pdf', '', 'EA974', '', '', dlabel,
                                              'c/kWh', value, 'NUoS', 'excl', '')
                detail, grid = self.compare([a, d])
                self.assertIsNone(grid)
                self.assertEqual({r['status'] for r in detail}, {'aer_only_component', 'dnsp_only_component'})

    def test_publication_precision_and_ordinary_rounding(self):
        detail, grid = self.compare([row('AER', '0.500'), row('DNSP', '0.540')])
        self.assertEqual(detail[0]['status'], 'value_differs')
        self.assertEqual(detail[0]['aer_value'], '0.500')
        self.assertEqual(grid['unexplained'], 1)
        detail, _ = self.compare([row('AER', '0.50004'), row('DNSP', '0.500')])
        self.assertEqual(detail[0]['status'], 'equal_after_rounding')
        detail, _ = self.compare([row('AER', '0.500'), row('DNSP', '0.500')])
        self.assertEqual(detail[0]['status'], 'equal')

    def test_swapped_bands_remain_discrepancies(self):
        detail, grid = self.compare([row('AER', '10.000'), row('AER', '5.000', band='offpeak'),
                                     row('DNSP', '5.000'), row('DNSP', '10.000', band='offpeak')])
        self.assertEqual(grid['unexplained'], 2)
        self.assertTrue(all(r['component'] == r['dnsp_component'] for r in detail))
        self.assertEqual({r['status'] for r in detail}, {'value_differs'})

    def test_incompatible_components_never_match_in_either_pass(self):
        variants = [dict(band='offpeak'), dict(season='summer'), dict(charge='export'),
                    dict(unit='c/kVAh'), dict(unit='c/kVA/day', charge='demand')]
        for value in ['5.000', '7.000']:
            for variant in variants:
                with self.subTest(value=value, variant=variant):
                    detail, grid = self.compare([row('AER', '5.000'), row('DNSP', value, **variant)])
                    self.assertIsNone(grid)
                    self.assertEqual({r['status'] for r in detail}, {'aer_only_component', 'dnsp_only_component'})
        for unit in ['c/kVA/day', 'c/kW/month', 'c/kW/season']:
            detail, grid = self.compare([row('AER', '5.000', unit='c/kW/day', charge='demand'),
                                        row('DNSP', '5.000', unit=unit, charge='demand')])
            self.assertIsNone(grid)

    def test_possible_causes_are_not_documented_explanations(self):
        for note, unit, charge, band in [('LFiT included', 'c/kWh', 'energy', 'peak'),
                                         ('includes metering', 'c/day', 'fixed', '')]:
            rows = [row(side, value, note=note if side == 'DNSP' else '', unit=unit, charge=charge, band=band)
                    for side, value in [('AER', '100.000'), ('DNSP', '110.000')]]
            detail, grid = self.compare(rows)
            self.assertEqual(detail[0]['explanation'], 'unexplained')
            self.assertEqual(grid['explainable'], 0)
            self.assertLess(grid['reconciled_rate'], 100)

    def test_uniform_offsets_stay_unexplained(self):
        rows = []
        for index in range(3):
            rows.extend([row('AER', f'{index + 1}.000', code=f'TEST{index}'),
                         row('DNSP', f'{index + 2}.000', code=f'TEST{index}')])
        detail, grid = self.compare(rows)
        self.assertEqual(grid['unexplained'], 3)
        self.assertEqual(grid['explainable'], 0)
        self.assertEqual({r['explanation'] for r in detail}, {'unexplained'})

    def test_power_and_water_season_abbreviations_compare(self):
        for alabel, dlabel, value in [('On Demand', 'On Season Demand Charge', '15.0000'),
                                      ('Off Demand', 'Off Season Demand Charge', '2.2000')]:
            with self.subTest(label=alabel):
                a = parse_aer.row('Power and Water Corporation', '2024-25', 'Tariff 5', 'LV Majors', '', alabel, '$/kVA',
                                  value, 'NUoS', 'aer.xlsx', '')
                d = sapn_pwc.make_row('Power and Water Corporation', '2024-25', 'DNSP', 'Tariff 5', 'LV Majors', '',
                                      dlabel, '$/kVA', value, 'excl', 'NUoS', 'dnsp.pdf', '', '')
                self.assertEqual(a['season'], d['season'])
                detail, grid = self.compare([a, d])
                self.assertEqual(grid['components_compared'], 1)
                self.assertEqual(detail[0]['status'], 'equal')

    def test_joint_code_matching_is_independent_of_hash_seed(self):
        rows = [row('AER', '5.000', code='010, 011*'), row('DNSP', '5.000', code='010'), row('DNSP', '5.000', code='011'),
                row('AER', '6.000', code='015, 016*'), row('DNSP', '6.000', code='015'), row('DNSP', '6.000', code='016')]
        outputs = []
        for seed in ('1', '2', '3'):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder).resolve()
                (root / 'out/dnsp').mkdir(parents=True)
                (root / 'sources').mkdir()
                (root / 'sources/inventory.csv').write_text('local_path\n')
                for filename, side in [('out/aer_long.csv', 'AER'), ('out/dnsp/test.csv', 'DNSP')]:
                    with (root / filename).open('w', newline='') as stream:
                        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
                        writer.writeheader()
                        writer.writerows(r for r in rows if r['side'] == side)
                code = ('import reconcile; reconcile.ROOT = %r; '
                        'reconcile.write_outputs(*reconcile.reconcile())' % str(root))
                subprocess.run([sys.executable, '-c', code], check=True, capture_output=True,
                               cwd=str(Path(__file__).resolve().parents[1] / 'scripts'),
                               env=dict(os.environ, PYTHONHASHSEED=seed))
                outputs.append((root / 'out/recon_detail.csv').read_bytes())
        self.assertEqual(len(set(outputs)), 1)

    def test_region_aliases_are_ergon_only(self):
        self.assertNotIn('HV', reconcile.code_alts('HVT1', distributor='Powercor'))
        self.assertIn('HV', reconcile.code_alts('HVT1', distributor='Ergon Energy'))
        for distributor, expected in [('Powercor', ''), ('Ergon Energy', 'region_variant')]:
            rows = [row('AER', '5.000', code='HV', distributor=distributor),
                    row('DNSP', '5.000', code='HV', distributor=distributor),
                    row('DNSP', '5.000', code='HVT1', distributor=distributor)]
            detail, _ = self.compare(rows)
            unmatched = next(r for r in detail if r['tariff_code'] == 'HVT1')
            self.assertEqual(unmatched['explanation'], expected)

    def test_excel_display_precision(self):
        cell = openpyxl.Workbook().active['A1']
        cell.value = 0.5
        cell.number_format = '0.000'
        self.assertEqual(cell_value(cell), '0.500')
        cell.number_format = '0.00E+00'
        self.assertEqual(cell_value(cell), '5.00E-01')
        self.assertEqual(reconcile.decimals_of(cell_value(cell)), 3)

    def test_missing_ocr_fails_explicitly(self):
        original = builtins.__import__
        def missing(name, *args, **kwargs):
            if name == 'rapidocr_onnxruntime':
                raise ModuleNotFoundError(name)
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=missing):
            with self.assertRaises(ModuleNotFoundError):
                essential_evoenergy.parse_evo_proposal('unused', '2023-24', 'AER_HOSTED', '', [])

    def test_removed_hash_mode_is_rejected(self):
        with self.assertRaises(SystemExit):
            fetch_sources.main(['--hash'])


if __name__ == '__main__':
    unittest.main()
