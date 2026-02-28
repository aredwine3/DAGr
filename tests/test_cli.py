import os
import tempfile
from typer.testing import CliRunner
from dagr.cli import app

runner = CliRunner()

def test_dagr_next_dopamine_menu(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.chdir(d)
        
        # Init
        runner.invoke(app, ["init", "--start", "2026-02-23"])
        
        # Add a critical task
        runner.invoke(app, ["add", "Main Task", "-d", "10.0"])
        
        # Add flexible tasks
        runner.invoke(app, ["add", "Quick flex", "-d", "0.5", "--flex", "--tag", "quick"])
        runner.invoke(app, ["add", "Low flex", "-d", "2.0", "--flex", "--tag", "low-energy"])
        runner.invoke(app, ["add", "Deep flex", "-d", "4.0", "--flex", "--tag", "hyperfocus"])
        runner.invoke(app, ["add", "Errands", "-d", "1.5", "--flex", "--tag", "errands"])
        
        # Run next
        result = runner.invoke(app, ["next"])
        
        assert "Dopamine Menu" in result.stdout, result.stdout
        assert "Quick Wins" in result.stdout
        assert "Quick flex" in result.stdout
        assert "Low Energy" in result.stdout
        assert "Low flex" in result.stdout
        assert "Hyperfocus" in result.stdout
        assert "Deep flex" in result.stdout
        assert "Other Side Quests" in result.stdout
        assert "Errands" in result.stdout
        assert "Main Task" in result.stdout # Should be next up
