"""
Supplementary tools for configuring LANraragi environments in Windows.
"""

import json
import logging
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
import redis


LOGGER = logging.getLogger(__name__)


class LRRWindowsEnvironment:
    """
    Set up a LANraragi environment in Windows using MSI installation.
    
    This class manages:
    - MSI installation/uninstallation
    - Karen process management 
    - Redis database configuration
    - LANraragi server startup/shutdown
    """
    
    def __init__(
        self, msi_path: str,
        init_with_api_key: bool = True, init_with_nofunmode: bool = True, init_with_allow_uploads: bool = True
    ):
        self.msi_path = Path(msi_path)
        self.init_with_api_key = init_with_api_key
        self.init_with_nofunmode = init_with_nofunmode
        self.init_with_allow_uploads = init_with_allow_uploads
        
        # Installation paths
        self.install_dir = Path(os.path.expandvars(r"%APPDATA%\LANraragi"))
        self.karen_exe = self.install_dir / "Karen.exe"
        self.lanraragi_dir = self.install_dir / "lanraragi"
        
        # Runtime paths
        self.content_dir = None
        self.thumb_dir = None
        self.database_dir = None
        self.temp_dir = None
        
        # Process tracking
        self.karen_process = None
        self.is_installed = False
        self.is_running = False
        
        # Redis connection
        self.redis_client = None
        
        # Configuration
        self.api_key = "lanraragi"
        self.port = 3001

    def _run_command(self, cmd: list, check: bool = True, capture_output: bool = True) -> subprocess.CompletedProcess:
        """
        Run a command and return the result.
        """
        LOGGER.debug(f"Running command: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, check=check, capture_output=capture_output, text=True)
            if result.stdout:
                LOGGER.debug(f"Command output: {result.stdout}")
            return result
        except subprocess.CalledProcessError as e:
            LOGGER.error(f"Command failed: {e}")
            if e.stdout:
                LOGGER.error(f"Stdout: {e.stdout}")
            if e.stderr:
                LOGGER.error(f"Stderr: {e.stderr}")
            raise

    def _check_existing_installation(self):
        """
        Check if LANraragi is already installed.
        """
        return self.karen_exe.exists()

    def _install_msi(self):
        """
        Install the LANraragi MSI package.
        """
        if not self.msi_path.exists():
            raise FileNotFoundError(f"MSI file not found: {self.msi_path}")
        if self._check_existing_installation():
            LOGGER.info("LANraragi already installed, skipping MSI installation")
            self.is_installed = True
            return
            
        LOGGER.info(f"Installing LANraragi MSI from {self.msi_path}")
        
        log_file = Path(tempfile.gettempdir()) / f"lanraragi_install_{uuid.uuid4().hex[:8]}.log"
        
        cmd = [
            "msiexec", "/i", str(self.msi_path), 
            "/quiet", "/norestart", "/qn",
            "REINSTALL=ALL", "REINSTALLMODE=vomus",
            "/l*v", str(log_file)
        ]
        
        result = self._run_command(cmd, capture_output=False)
        
        # Check if installation was successful (exit codes 0 or 3010 are success)
        if result.returncode not in [0, 3010]:
            # Read the log file for more details
            if log_file.exists():
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    log_content = f.read()
                    LOGGER.error(f"MSI installation failed. Log content:\n{log_content}")
            raise RuntimeError(f"MSI installation failed with exit code {result.returncode}")
        max_wait = 60  # Increased timeout for slow systems
        for i in range(max_wait):
            if self.karen_exe.exists():
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"Karen.exe not found after {max_wait} seconds. Installation may have failed.")
            
        self.is_installed = True
        LOGGER.info("LANraragi MSI installation completed successfully")

    def _uninstall_msi(self):
        """Uninstall the LANraragi MSI package."""
        if not self.is_installed:
            return
            
        LOGGER.info("Uninstalling LANraragi MSI")
        # First try to find the product code from the registry or use generic uninstall
        # For now, we'll use the MSI file path for uninstallation
        cmd = [
            "msiexec", "/x", str(self.msi_path),
            "/quiet", "/norestart"
        ]
        
        try:
            self._run_command(cmd, check=False, capture_output=False)
        except Exception as e:
            LOGGER.warning(f"MSI uninstallation failed, but continuing: {e}")
            
        # Manual cleanup if MSI uninstall doesn't work perfectly
        if self.install_dir.exists():
            try:
                import shutil
                shutil.rmtree(self.install_dir, ignore_errors=True)
                LOGGER.info("Manual cleanup of installation directory completed")
            except Exception as e:
                LOGGER.warning(f"Manual cleanup failed: {e}")
                
        self.is_installed = False

    def _setup_directories(self):
        """
        Set up temporary directories for LANraragi data.
        """
        self.temp_dir = Path(tempfile.mkdtemp(prefix="lrr_test_"))
        self.content_dir = self.temp_dir / "content"
        self.thumb_dir = self.temp_dir / "thumb"  
        self.database_dir = self.temp_dir / "database"
        for dir_path in [self.content_dir, self.thumb_dir, self.database_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
            
        LOGGER.info(f"Created test directories in {self.temp_dir}")

    def _configure_karen_settings(self):
        """Configure Karen settings before starting the process."""
        settings_file = self.install_dir / "settings.json"
        
        # Create settings configuration
        settings = {
            "contentFolder": str(self.content_dir),
            "thumbnailFolder": str(self.thumb_dir),
            "networkPort": self.port,
            "startServerAutomatically": True,
            "firstLaunch": False,
            "forceDebugMode": True
        }
        
        # Write settings file
        with open(settings_file, 'w') as f:
            json.dump(settings, f, indent=2)
            
        LOGGER.info(f"Karen settings configured: {settings}")

    def _start_karen_process(self):
        """Start the Karen process which manages LANraragi."""
        if not self.karen_exe.exists():
            raise FileNotFoundError(f"Karen executable not found: {self.karen_exe}")
        self._configure_karen_settings()
        cmd = [str(self.karen_exe)]
        LOGGER.info(f"Starting Karen process: {' '.join(cmd)}")
        env = os.environ.copy()
        env.update({
            "LRR_DATA_DIRECTORY": str(self.content_dir),
            "LRR_THUMB_DIRECTORY": str(self.thumb_dir),
            "LRR_NETWORK": f"http://localhost:{self.port}"
        })
        self.karen_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.install_dir,
            env=env
        )
        time.sleep(10)  # Give more time for Karen to start LANraragi
        
        if self.karen_process.poll() is not None:
            stdout, stderr = self.karen_process.communicate()
            raise RuntimeError(f"Karen process failed to start. Stdout: {stdout}, Stderr: {stderr}")
            
        self.is_running = True
        LOGGER.info("Karen process started successfully")

    def _stop_karen_process(self):
        """
        Stop the Karen process and associated LANraragi processes.
        """
        if not self.is_running:
            return

        LOGGER.info("Stopping Karen and LANraragi processes")

        if self.karen_process and self.karen_process.poll() is None:
            try:
                self.karen_process.terminate()
                self.karen_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                LOGGER.warning("Karen process didn't terminate gracefully, killing it")
                self.karen_process.kill()
                self.karen_process.wait()

        processes_to_kill = ["perl.exe", "redis-server.exe", "Karen.exe"]
        for proc_name in processes_to_kill:
            try:
                subprocess.run(["taskkill", "/F", "/IM", proc_name], 
                             capture_output=True, check=False)
            except Exception as e:
                LOGGER.debug(f"Failed to kill {proc_name}: {e}")
                
        self.is_running = False
        self.karen_process = None
        LOGGER.info("LANraragi processes stopped")

    def _connect_to_redis(self):
        """Connect to the LANraragi Redis instance."""
        try:
            self.redis_client = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
            self.redis_client.ping()
            LOGGER.info("Connected to Redis successfully")
        except Exception as e:
            LOGGER.error(f"Failed to connect to Redis: {e}")
            self.redis_client = None

    def _wait_for_server(self, max_retries: int = 30):
        """Wait for LANraragi server to be ready."""
        url = f"http://localhost:{self.port}"
        
        for i in range(max_retries):
            try:
                import requests
                response = requests.get(f"{url}/api/info", timeout=5)
                if response.status_code == 200:
                    LOGGER.info("LANraragi server is ready")
                    return
            except Exception as e:
                LOGGER.debug(f"Server not ready yet (attempt {i+1}): {e}")
                time.sleep(2)
                
        raise RuntimeError(f"LANraragi server not ready after {max_retries} attempts")

    def add_api_key(self, api_key: str):
        """Add an API key to LANraragi configuration."""
        if not self.redis_client:
            self._connect_to_redis()
            
        if self.redis_client:
            self.redis_client.select(2)  # Config database
            self.redis_client.set("apikey", api_key)
            self.api_key = api_key
            LOGGER.info(f"API key set to: {api_key}")
        else:
            LOGGER.warning("Could not set API key - Redis not available")

    def enable_nofun_mode(self):
        """Enable nofun mode in LANraragi."""
        if not self.redis_client:
            self._connect_to_redis()
            
        if self.redis_client:
            self.redis_client.select(2)
            self.redis_client.set("nofunmode", "1")
            LOGGER.info("Nofun mode enabled")

    def disable_nofun_mode(self):
        """Disable nofun mode in LANraragi."""
        if not self.redis_client:
            self._connect_to_redis()
            
        if self.redis_client:
            self.redis_client.select(2)
            self.redis_client.set("nofunmode", "0")
            LOGGER.info("Nofun mode disabled")

    def _enable_uploads(self):
        """Enable file uploads in LANraragi."""
        if not self.redis_client:
            self._connect_to_redis()
            
        if self.redis_client:
            self.redis_client.select(2)
            self.redis_client.set("enableupload", "1")
            LOGGER.info("File uploads enabled")

    def get_lrr_logs(self, tail: int = 100) -> bytes:
        """Get the LANraragi logs."""
        log_file = self.lanraragi_dir / "log" / "lanraragi.log"
        if log_file.exists():
            try:
                with open(log_file, 'rb') as f:
                    lines = f.readlines()
                    return b''.join(lines[-tail:])
            except Exception as e:
                LOGGER.error(f"Failed to read log file: {e}")
        return b"No logs available"

    def display_lrr_logs(self, tail: int = 100, log_level: int = logging.ERROR):
        """Display LANraragi logs."""
        logs = self.get_lrr_logs(tail)
        if logs:
            LOGGER.log(log_level, f"LANraragi logs (last {tail} lines):\n{logs.decode('utf-8', errors='ignore')}")

    def setup(self, test_connection_max_retries: int = 4):
        """Set up the Windows LANraragi environment."""
        try:
            LOGGER.info("Setting up Windows LANraragi environment")
            self._install_msi()
            self._setup_directories() 
            self._start_karen_process()
            self._wait_for_server(test_connection_max_retries * 10)
            self._connect_to_redis()
            
            if self.init_with_api_key:
                self.add_api_key(self.api_key)
                
            if self.init_with_nofunmode:
                self.enable_nofun_mode()
                
            if self.init_with_allow_uploads:
                self._enable_uploads()
                
            LOGGER.info("Windows LANraragi environment setup completed successfully")
            
        except Exception as e:
            LOGGER.error(f"Failed to setup Windows environment: {e}")
            try:
                self.teardown()
            except Exception as cleanup_error:
                LOGGER.error(f"Failed to cleanup after setup failure: {cleanup_error}")
            raise

    def teardown(self):
        """Tear down the Windows LANraragi environment."""
        LOGGER.info("Tearing down Windows LANraragi environment")
        
        try:
            self._stop_karen_process()
            if self.redis_client:
                try:
                    self.redis_client.close()
                except Exception as e:
                    LOGGER.debug(f"Error closing Redis connection: {e}")
                self.redis_client = None
            if self.temp_dir and self.temp_dir.exists():
                try:
                    import shutil
                    shutil.rmtree(self.temp_dir, ignore_errors=True)
                    LOGGER.info("Temporary directories cleaned up")
                except Exception as e:
                    LOGGER.warning(f"Failed to clean up temporary directories: {e}")
            self._uninstall_msi()
            
            LOGGER.info("Windows LANraragi environment teardown completed")
            
        except Exception as e:
            LOGGER.error(f"Error during teardown: {e}")
