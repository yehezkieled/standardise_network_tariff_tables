import builtins
import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import openpyxl
import reconcile
from published import cell_value
from schema import COLUMNS
from units import to_std
from dnsp import essential_evoenergy
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
