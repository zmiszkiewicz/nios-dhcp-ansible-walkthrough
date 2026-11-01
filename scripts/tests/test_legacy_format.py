"""The mac.txt format: Ansible's template against what the original PowerShell produces."""
import re
import shutil
import subprocess
from pathlib import Path

import jinja2
import pytest

REPO = Path(__file__).resolve().parents[2]
LEGACY = REPO / "legacy"
TEMPLATES = REPO / "ansible" / "templates"
SAMPLE_MACS = ["00:50:56:aa:bb:01", "00:50:56:aa:bb:02", "00:50:56:aa:bb:0a", "00:50:56:aa:bb:10", "00:50:56:aa:bb:ff"]
PWSH = shutil.which("pwsh") or str(Path.home() / ".local" / "powershell" / "pwsh")


def render(lab_filters, name, newline="\r\n", **variables):
    """Render a template the way Ansible's template module does (trim_blocks, CRLF on request)."""
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)),
                             trim_blocks=True, keep_trailing_newline=True)
    env.filters.update(lab_filters.FilterModule().filters())
    text = env.get_template(name).render(**variables)
    return text.replace("\n", newline).encode()


def test_template_reproduces_the_sample_output(lab_filters):
    got = render(lab_filters, "mac.txt.j2", mac_list=SAMPLE_MACS, mac_legacy_blank_lines=2)
    assert got == (LEGACY / "samples" / "mac.sample.txt").read_bytes()


def test_template_reproduces_the_sample_export(lab_filters):
    got = render(lab_filters, "ms-lease-export.csv.j2", mac_list=SAMPLE_MACS)
    assert got == (LEGACY / "samples" / "leases-export.sample.csv").read_bytes()


def test_a_clean_file_is_one_setting_away(lab_filters):
    got = render(lab_filters, "mac.txt.j2", mac_list=SAMPLE_MACS[:2], mac_legacy_blank_lines=0)
    assert got == b"00:50:56:aa:bb:01\r\n00:50:56:aa:bb:02\r\n"


def test_empty_scope_gives_only_the_blank_lines(lab_filters):
    assert render(lab_filters, "mac.txt.j2", mac_list=[], mac_legacy_blank_lines=2) == b"\r\n\r\n"


def test_the_runner_keeps_the_original_replace_pipeline_verbatim():
    pipeline = re.compile(r"Foreach-Object \{\$_ -replace .*?\}")
    original = pipeline.search((LEGACY / "powershell" / "Push-MacList.original.ps1").read_text()).group(0)
    runner = pipeline.search((LEGACY / "powershell" / "Normalize-ScopeExport.ps1").read_text()).group(0)
    assert runner == original


@pytest.mark.skipif(not Path(PWSH).exists(), reason="PowerShell is not installed")
def test_real_powershell_produces_the_same_content(lab_filters, tmp_path):
    csv = render(lab_filters, "ms-lease-export.csv.j2", mac_list=SAMPLE_MACS)
    (tmp_path / "10.100.11.0.csv").write_bytes(csv)
    subprocess.run([PWSH, "-NoProfile", "-File", str(LEGACY / "powershell" / "Normalize-ScopeExport.ps1"),
                    "-ScopeId", "10.100.11.0", "-Dir", str(tmp_path)], check=True, capture_output=True)
    powershell = (tmp_path / "10.100.11.0.txt").read_bytes()
    ansible = render(lab_filters, "mac.txt.j2", mac_list=SAMPLE_MACS, mac_legacy_blank_lines=2)
    # PowerShell on macOS writes LF; Windows PowerShell writes CRLF (checked live by compare_legacy.yml)
    assert powershell.replace(b"\r\n", b"\n") == ansible.replace(b"\r\n", b"\n")
