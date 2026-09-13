from app.admin_surface_patch import _decorate, _is_admin_html_route


def test_admin_html_routes_are_guarded_but_admin_api_is_not():
    assert _is_admin_html_route('/admin', {'GET'}, False)
    assert _is_admin_html_route('/admin/users', {'GET'}, False)
    assert not _is_admin_html_route('/admin/users', {'GET'}, True)
    assert not _is_admin_html_route('/v1/admin/users', {'GET'}, False)


def test_admin_page_gets_one_persistent_shell_and_token_ui_is_hidden():
    source = '''<!doctype html><html><head><title>X1 Admin Control Center</title><style nonce="abc"></style></head><body><section class="card"><input id="token"></section></body></html>'''
    rendered = _decorate(source)
    assert rendered.count('x1-admin-global') >= 1
    assert 'x1-admin-global-links' in rendered
    assert 'section:has(#token)' in rendered
    assert '<style nonce="abc">' in rendered
    assert _decorate(rendered) == rendered


def test_admin_active_section_follows_page_title():
    source = '''<!doctype html><html><head><title>X1 Admin · Users</title></head><body><a href="/admin">Control</a></body></html>'''
    rendered = _decorate(source)
    assert 'class="x1-admin-nav-link active" href="/admin/users"' in rendered
