from app.admin_shell_patch import decorate_admin_page


def test_admin_shell_injected_once_with_template_nonce():
    page = '<!doctype html><html><head><style nonce="__NONCE__">body{}</style></head><body><header class="top">old</header><main>ok</main></body></html>'
    rendered = decorate_admin_page(page)

    assert rendered.count('id="x1-admin-shell"') == 1
    assert '<style nonce="__NONCE__">' in rendered
    assert '<script nonce="__NONCE__">' in rendered
    assert 'sessionStorage.getItem(\'x1_access_token\')' in rendered
    assert '/admin/integrations' in rendered
    assert '/admin/users' in rendered

    assert decorate_admin_page(rendered) == rendered


def test_admin_shell_reuses_runtime_nonce():
    page = '<html><head><style nonce="runtime-nonce">body{}</style></head><body><main>ok</main></body></html>'
    rendered = decorate_admin_page(page)

    assert '<style nonce="runtime-nonce">' in rendered
    assert '<script nonce="runtime-nonce">' in rendered
    assert 'aria-label="Административное меню"' in rendered


def test_non_html_payload_is_left_untouched():
    value = '{"status":"ok"}'
    assert decorate_admin_page(value) == value
