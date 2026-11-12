import json
from pathlib import Path
import subprocess
import unittest


class DesktopWarmupTests(unittest.TestCase):
    def test_desktop_describes_warmup_instead_of_disabled(self):
        script = r'''
const fs = require('fs'), vm = require('vm');
const source = fs.readFileSync('plugin/desktop/plugin.js', 'utf8');
const body = source.slice(source.indexOf('function describe('), source.indexOf('function useStatus('));
process.stdout.write(JSON.stringify(vm.runInNewContext(body + '\ndescribe({state:"warming_model",enabled:true,model:"project",model_readiness:"loaded"})')));
'''
        result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding='utf-8', timeout=10, check=True)
        self.assertIn('预热', json.loads(result.stdout))


if __name__ == '__main__':
    unittest.main()
