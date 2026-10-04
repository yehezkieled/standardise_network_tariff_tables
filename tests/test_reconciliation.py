import builtins
import csv
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import openpyxl
import reconcile
import parse_aer
from published import cell_value
from schema import COLUMNS
from units import to_std
from dnsp import essential_evoenergy, ausgrid_endeavour, cp_pc_ue
import fetch_sources


def row(side, value, band='peak', unit='c/kWh', charge='energy', season='', code='TEST', note='', distributor='Evoenergy'):
    result = dict.fromkeys(COLUMNS, '')
    std, std_unit = to_std(value, unit)
    result.update(side=side, distributor=distributor, fin_year='2025-26', tariff_code=code,
                  tariff_name=code, component=f'{season} {band} {charge}'.strip(), charge_type=charge,
                  time_band=band, season=season, unit=unit, value=value, value_std=std,
                  unit_std=std_unit, gst='excl', basis='NUoS', source_file=side+'.pdf', note=note)
    return result


class ReconciliationRegression(unittest.TestCase):
    def compare(self, rows):
        with tempfile.TemporaryDirectory(dir='notes/scratch') as folder:
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
