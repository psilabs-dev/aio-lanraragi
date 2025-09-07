"""
Simple Windows integration test for LANraragi.

This test verifies that the Windows build process produces the expected output files.
"""

import os
import pytest
from pathlib import Path

@pytest.mark.skipif(os.name != "nt", reason="Windows-specific test")
def test_windows_build_outputs_exist():
    """
    Test that the Windows build process creates the expected MSI and executable files.
    
    This test checks for:
    1. LANraragi.msi installer file
    2. Karen.exe bootstrapper executable
    """
    # Get paths from environment variables set by GitHub Actions
    msi_path = os.environ.get("LANraragi_MSI_PATH")
    karen_path = os.environ.get("LANraragi_KAREN_PATH")
    
    # Fallback to default paths if environment variables are not set
    if not msi_path:
        msi_path = "../../LANraragi/tools/build/windows/Karen/Setup/bin/LANraragi.msi"
    if not karen_path:
        karen_path = "../../LANraragi/tools/build/windows/Karen/Karen/bin/win-x64/publish/Karen.exe"
    
    msi_file = Path(msi_path)
    karen_file = Path(karen_path)
    
    # Test that MSI installer exists
    assert msi_file.exists(), f"LANraragi MSI installer not found at: {msi_file.absolute()}"
    assert msi_file.is_file(), f"MSI path exists but is not a file: {msi_file.absolute()}"
    assert msi_file.suffix.lower() == ".msi", f"Expected .msi file, got: {msi_file.suffix}"
    
    # Test that MSI is not empty
    msi_size = msi_file.stat().st_size
    assert msi_size > 1024 * 1024, f"MSI file seems too small ({msi_size} bytes), expected > 1MB"
    
    # Test that Karen executable exists
    assert karen_file.exists(), f"Karen executable not found at: {karen_file.absolute()}"
    assert karen_file.is_file(), f"Karen path exists but is not a file: {karen_file.absolute()}"
    assert karen_file.suffix.lower() == ".exe", f"Expected .exe file, got: {karen_file.suffix}"
    
    # Test that Karen executable is not empty
    karen_size = karen_file.stat().st_size
    assert karen_size > 100 * 1024, f"Karen executable seems too small ({karen_size} bytes), expected > 100KB"


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific test")  
def test_windows_build_directory_structure():
    """
    Test that the Windows build creates the expected directory structure.
    """
    karen_dir = Path(os.environ.get("LANraragi_KAREN_PATH", "../../LANraragi/tools/build/windows/Karen/Karen/bin/win-x64/publish")).parent
    
    # Check for key runtime files that should be present
    expected_files = [
        "Karen.exe",
        "lanraragi/runtime/bin/perl.exe",
        "lanraragi/runtime/redis/redis-server.exe", 
        "lanraragi/lib/LANraragi.pm",
        "lanraragi/script/lanraragi",
        "lanraragi/lrr.conf"
    ]
    
    for expected_file in expected_files:
        file_path = karen_dir / expected_file
        assert file_path.exists(), f"Expected file not found: {file_path.absolute()}"
