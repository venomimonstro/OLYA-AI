from app.admin_surface_patch import _decorate, _is_admin_html_route


def test_admin_html_routes_are_guarded_but_login_and_admin_api_are_not():
    assert _is_admin_html_route('/admin', {'GET'}, False)
    assert _is_admin_html_route('/admin/users', {'GET'}, False)
    assert not _is_admin_html_route('/admin/login', {'GET'}, False)
    assert not _is_admin_html_route('/admin/users', {'GET'}, True)
    assert not _is_admin_html_route('/v1/admin/users', {'GET'}, False)


def test_admin_page_gets_one_persistent_shell_and_only_manual_token_controls_are_hidden():
    source = '''<!doctype html><html><head><title>X1 Admin Control Center</title><style nonce="abc"></style></head><body><header>legacy</header><section class="card"><div class="row"><input id="token"><button id="connect">open</button><button id="save">save</button></div></section></body></html>'''
    rendered = _decorate(source)
    assert rendered.count('x1-admin-global') >= 1
    assert 'x1-admin-global-links' in rendered
    assert '#token,#connect,#token+button' in rendered
    assert 'section:has(#token)' not in rendered
    assert 'body>header{display:none!important}' in rendered
    assert '<button id="save">save</button>' in rendered
    assert '<style nonce="abc">' in rendered
    assert '/admin/login?next=' in rendered
    assert _decorate(rendered) == rendered


def test_pure_legacy_token_card_is_hidden_without_hiding_settings_cards():
    source = '''<!doctype html><html><head><title>X1 Public Launch</title></head><body><div class="card"><b>Админ-токен</b><input id="token"><button>Открыть</button></div><div class="card"><button id="important">Сохранить</button></div></body></html>'''
    rendered = _decorate(source)
    assert '.card:has(>b + #token){display:none!important}' in rendered
    assert '<button id="important">Сохранить</button>' in rendered


def test_admin_active_section_follows_page_title():
    source = '''<!doctype html><html><head><title>X1 Admin · Users</title></head><body><a href="/admin">Control</a></body></html>'''
    rendered = _decorate(source)
    assert 'class="x1-admin-nav-link active" href="/admin/users"' in rendered
