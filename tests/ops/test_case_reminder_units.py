from pathlib import Path
import os
import subprocess

ROOT=Path(__file__).resolve().parents[2]


def test_installs_only_case_units_without_activating_delivery(tmp_path):
    release=tmp_path/'release';release.mkdir()
    private=tmp_path/'private';private.mkdir()
    runtime=tmp_path/'runtime.env';runtime.write_text('AUTONOMO_PRIVATE_ROOT='+str(private)+'\n')
    binaries=tmp_path/'bin';binaries.mkdir()
    log=tmp_path/'commands'
    control=binaries/'systemctl';control.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_SYSTEMCTL_LOG"\n');control.chmod(0o755)
    config=tmp_path/'config';units=config/'systemd/user';units.mkdir(parents=True)
    backup=units/'autonomo-backup.timer';backup.write_text('preserve existing timer')
    env={**os.environ,'PATH':str(binaries)+os.pathsep+os.environ['PATH'],'XDG_CONFIG_HOME':str(config),'TEST_SYSTEMCTL_LOG':str(log)}
    result=subprocess.run(['bash',str(ROOT/'ops/install-case-reminder-units.sh'),str(release),str(private),str(runtime)],env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert log.read_text().splitlines()==['--user daemon-reload']
    service=(units/'autonomo-case-reminders.service').read_text()
    assert '@' not in service and str(runtime) in service
    assert 'government-cases reminders' in service
    assert 'OnCalendar=hourly' in (units/'autonomo-case-reminders.timer').read_text()
    assert backup.read_text()=='preserve existing timer'


def test_rejects_missing_or_unsafe_runtime_before_writing_units(tmp_path):
    result=subprocess.run(['bash',str(ROOT/'ops/install-case-reminder-units.sh'),'relative','/private','/missing'],env={**os.environ,'XDG_CONFIG_HOME':str(tmp_path/'config')},capture_output=True)
    assert result.returncode!=0
    assert not (tmp_path/'config').exists()
