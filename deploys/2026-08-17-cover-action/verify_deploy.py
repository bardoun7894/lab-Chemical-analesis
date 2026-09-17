import sys
sys.path.insert(0, '/app/app')
from app import create_app
from app.models.user import User

app = create_app()
with app.test_client() as c, app.app_context():
    u = User.query.filter_by(username='admin').first()
    with c.session_transaction() as sess:
        sess['_user_id'] = str(u.id)
        sess['_fresh'] = True
    resp = c.get('/reports/non-conformance')
    html = resp.get_data(as_text=True)

    print('action/save URL present:', '/reports/non-conformance/action/save' in html)
    print('data-bs-target ~ #coverModal:', 'data-bs-target=' in html and '#coverModal' in html)
    import re
    m = re.search(r"COVER_SAVE_URL = \"([^\"]+)\"", html)
    print('COVER_SAVE_URL value:', m.group(1) if m else '(not found)')
    print('JS has coverModal listener:', 'coverModal' in html)
    print('JS has show.bs.modal handler:', 'show.bs.modal' in html)
    print()
    print('=== POST endpoint test ===')
    r = c.post('/reports/non-conformance/action/save', json={
        'source': 'chemical', 'source_code': 'DEPLOY_TEST_LADLE',
        'root_cause': 'verify live deploy',
        'corrective_action': 'n/a',
        'responsible': 'deploy-bot',
        'responsible_date': '2026-08-17',
        'status': 'Done',
    })
    print('  POST HTTP:', r.status_code)
    body = r.get_json()
    print('  success:', body.get('success'))
    print('  status:', body.get('status'))
    print('  action.source:', body.get('action', {}).get('source'))
    print('  action.source_code:', body.get('action', {}).get('source_code'))

    # Cleanup the test row
    from app.models.nonconformance_action import NonConformanceAction
    a = NonConformanceAction.query.filter_by(
        source='chemical', source_code='DEPLOY_TEST_LADLE'
    ).first()
    if a:
        from app import db
        db.session.delete(a)
        db.session.commit()
        print('  cleaned up test row')