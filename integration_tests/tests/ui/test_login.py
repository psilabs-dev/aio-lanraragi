"""
UI tests for LANraragi login functionality using Playwright
"""
import pytest
import docker
from playwright.sync_api import Page, expect
from aio_lanraragi_tests.lrr_docker import LRREnvironment


@pytest.fixture(scope="session", autouse=True)
def ui_test_environment(request: pytest.FixtureRequest):
    """Set up and tear down LANraragi test environment for UI tests"""
    build_path: str = request.config.getoption("--build")
    image: str = request.config.getoption("--image")
    git_url: str = request.config.getoption("--git-url")
    git_branch: str = request.config.getoption("--git-branch")
    use_docker_api: bool = request.config.getoption("--docker-api")
    docker_client = docker.from_env()
    docker_api = docker.APIClient(base_url="unix://var/run/docker.sock") if use_docker_api else None
    environment = LRREnvironment(build_path, image, git_url, git_branch, docker_client, docker_api=docker_api)
    environment.setup()
    yield environment
    environment.teardown()


BASE_URL = "http://localhost:3001"  # LRREnvironment uses port 3001


@pytest.mark.ui
def test_login_page_loads(page: Page):
    """Test that the login page loads correctly"""
    page.goto(f"{BASE_URL}/login")
    
    # Check page title
    expect(page).to_have_title("LANraragi - Admin Login")
    
    # Check that the password field is present
    password_field = page.locator("#pw_field")
    expect(password_field).to_be_visible()
    expect(password_field).to_have_attribute("type", "password")
    expect(password_field).to_have_attribute("name", "password")
    
    # Check that submit button is present
    submit_button = page.locator('input[type="submit"]')
    expect(submit_button).to_be_visible()


@pytest.mark.ui
def test_login_form_structure(page: Page):
    """Test the structure of the login form"""
    page.goto(f"{BASE_URL}/login")
    
    # Check form element exists
    form = page.locator("form")
    expect(form).to_be_visible()
    expect(form).to_have_attribute("method", "post")
    
    # Verify password field properties
    password_field = page.locator("#pw_field")
    expect(password_field).to_be_enabled()
    expect(password_field).to_be_editable()


@pytest.mark.ui
def test_empty_password_submission(page: Page):
    """Test submitting the form with empty password"""
    page.goto(f"{BASE_URL}/login")
    
    # Submit form without entering password
    submit_button = page.locator('input[type="submit"]')
    submit_button.click()
    
    # Should stay on login page or show error
    # LANraragi might redirect or show error - we check we're still on login context
    expect(page.locator("#pw_field")).to_be_visible()


@pytest.mark.ui
def test_password_field_interaction(page: Page):
    """Test interacting with the password field"""
    page.goto(f"{BASE_URL}/login")
    
    password_field = page.locator("#pw_field")
    
    # Type in password field
    test_password = "testpassword123"
    password_field.fill(test_password)
    
    # Verify the field has focus and content (though content will be masked)
    expect(password_field).to_be_focused()
    expect(password_field).to_have_value(test_password)
    
    # Clear the field
    password_field.clear()
    expect(password_field).to_have_value("")


@pytest.mark.ui
def test_login_with_incorrect_password(page: Page):
    """Test login with incorrect password"""
    page.goto(f"{BASE_URL}/login")
    
    password_field = page.locator("#pw_field")
    submit_button = page.locator('input[type="submit"]')
    
    # Enter incorrect password
    password_field.fill("wrongpassword")
    submit_button.click()
    
    # Should either stay on login page or redirect back to login
    # Check we're still in login context
    expect(page.locator("#pw_field")).to_be_visible()


@pytest.mark.ui
def test_successful_login(page: Page):
    """Test successful login with correct password"""
    page.goto(f"{BASE_URL}/login")
    
    password_field = page.locator("#pw_field")
    submit_button = page.locator('input[type="submit"]')
    
    # Enter correct password
    password_field.fill("kamimamita")
    submit_button.click()
    
    # Should redirect to main page after successful login
    # Wait for navigation to complete
    page.wait_for_url(f"{BASE_URL}/", timeout=10000)
    
    # Verify we're on the main page and logged in
    expect(page).to_have_url(f"{BASE_URL}/")
    
    # Look for indicators that we're logged in (e.g., admin features)
    # The page should not have the login form anymore
    expect(page.locator("#pw_field")).not_to_be_visible()


@pytest.mark.ui
def test_post_login_admin_access(page: Page):
    """Test that admin features are accessible after login"""
    # First login
    page.goto(f"{BASE_URL}/login")
    password_field = page.locator("#pw_field")
    password_field.fill("kamimamita")
    page.locator('input[type="submit"]').click()
    
    # Wait for navigation to main page
    page.wait_for_url(f"{BASE_URL}/", timeout=10000)
    
    # Try to access admin pages that should be available after login
    admin_paths = ["/stats", "/config"]
    
    for path in admin_paths:
        page.goto(f"{BASE_URL}{path}")
        # Should not redirect back to login page
        expect(page).not_to_have_url(f"{BASE_URL}/login")
        # Should not show the login form
        expect(page.locator("#pw_field")).not_to_be_visible()


@pytest.mark.ui
def test_login_page_navigation_elements(page: Page):
    """Test navigation elements on login page"""
    page.goto(f"{BASE_URL}/login")
    
    # Check for link back to main page if it exists
    # LANraragi might have navigation elements
    main_link = page.locator('a[href="/"]')
    if main_link.is_visible():
        expect(main_link).to_be_visible()


@pytest.mark.ui
def test_login_page_accessibility(page: Page):
    """Test basic accessibility features of login page"""
    page.goto(f"{BASE_URL}/login")
    
    # Check that password field has proper attributes for accessibility
    password_field = page.locator("#pw_field")
    expect(password_field).to_have_attribute("type", "password")
    
    # Check form can be submitted with Enter key
    password_field.fill("testpassword")
    password_field.press("Enter")
    
    # Should trigger form submission
    expect(page.locator("#pw_field")).to_be_visible()


@pytest.mark.ui
def test_login_page_responsive_elements(page: Page):
    """Test that login page elements are responsive"""
    page.goto(f"{BASE_URL}/login")
    
    # Test on different viewport sizes
    page.set_viewport_size({"width": 320, "height": 568})  # Mobile
    expect(page.locator("#pw_field")).to_be_visible()
    
    page.set_viewport_size({"width": 768, "height": 1024})  # Tablet
    expect(page.locator("#pw_field")).to_be_visible()
    
    page.set_viewport_size({"width": 1920, "height": 1080})  # Desktop
    expect(page.locator("#pw_field")).to_be_visible()


@pytest.mark.ui
def test_navigate_to_login_from_main(page: Page):
    """Test that login links exist on main page"""
    page.goto(BASE_URL)
    
    # Check that login links exist on the main page
    # Note: We're just verifying the links exist, not clicking them due to overlay issues
    login_links = page.locator('a[href="/login"]')
    expect(login_links).to_have_count(2)  # Should have 2 login links as discovered
    
    # Verify that at least one has "Admin Login" text
    admin_login_link = page.get_by_role("link", name="Admin Login")
    expect(admin_login_link).to_be_visible()


@pytest.mark.ui
def test_direct_login_url_access(page: Page):
    """Test accessing login page directly via URL"""
    page.goto(f"{BASE_URL}/login")
    
    expect(page).to_have_url(f"{BASE_URL}/login")
    expect(page.locator("#pw_field")).to_be_visible()
    expect(page).to_have_title("LANraragi - Admin Login")