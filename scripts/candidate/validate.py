"""Live, authenticated V13.3 acceptance checks against candidate identity only."""
import json
from pathlib import Path
import ssl
import time
from uuid import uuid4

import httpx

from scripts.candidate.manage import DEFAULT, PROJECT, compose, verify
from scripts.candidate.runtime import api_python, credentials, http, snapshot, token


def main():
    manifest = verify(DEFAULT)
    prefix = 'v133-' + uuid4().hex[:10]
    observations = []
    path = DEFAULT / 'validation-results.json'

    def save():
        path.write_text(json.dumps(observations, ensure_ascii=False, indent=2))
        path.chmod(0o600)

    def record(name, status='PASS', **details):
        observations.append({'scenario': name, 'status': status, **details})
        save()
        print(json.dumps(observations[-1], ensure_ascii=False), flush=True)

    def ask(client, message, thread='advice'):
        started = time.perf_counter()
        response = client.post('/assistant', json={'message': message, 'thread_id': prefix + '-' + thread})
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        assert response.status_code == 200, (response.status_code, response.text[:180])
        result = response.json()
        observations.append({'scenario': 'request', 'status': 'PASS', 'message': message,
            'request_id': response.headers.get('x-request-id'), 'total_ms': elapsed,
            'intent': result.get('intent'), 'advice_status': result.get('advice_status'),
            'skus': [p['sku'] for p in result.get('recommendations', [])],
            'budget': result.get('budget'), 'order_status': result.get('order_status')})
        save()
        return result

    def verify_cards(result):
        for card in result.get('recommendations', []):
            actual = customer.get('/products/' + card['sku'])
            assert actual.status_code == 200
            actual = actual.json()
            assert all(card[k] == actual[k] for k in ('sku','product_name','category','price','stock'))
            assert 'UNVERIFIED' not in card['explanation'] and '999W' not in card['explanation']

    before = snapshot()
    try:
        with http() as customer, http('candidate-v133-b') as other:
            first = ask(customer, 'Je cherche une perceuse disponible à moins de 100 euros.')
            assert first['intent'] == 'PRODUCT_ADVICE'
            assert [p['sku'] for p in first['recommendations']] == ['D-A','D-B','D-C']
            assert all(p['price'] < 100 and p['stock'] > 0 for p in first['recommendations'])
            assert 'modèle est indisponible' not in first['response']
            verify_cards(first)
            record('A_general_real_ollama', facts=[{k:p[k] for k in ('sku','price','stock')} for p in first['recommendations']])
            targeted = ask(customer, 'Peux-tu me conseiller sur la Perceuse Concurrence Synthétique V017 ?', 'target')
            assert [p['sku'] for p in targeted['recommendations']] == ['D-C']
            verify_cards(targeted)
            record('B_targeted_real_ollama')
            compare = ask(customer, 'Compare cette perceuse avec deux autres.', 'target')
            assert [p['sku'] for p in compare['recommendations']] == ['D-C','D-A','D-B']
            assert [p['role'] for p in compare['recommendations']] == ['reference','alternative','alternative']
            verify_cards(compare)
            record('C_comparison_real_ollama')
            ambiguous = ask(customer, 'Compare cette perceuse avec deux autres.', 'target')
            assert ambiguous['advice_status'] == 'CLARIFICATION_REQUIRED' and not ambiguous['recommendations']
            for question, expected in [('Conseille-moi sur la Perceuse Pro V017.', 'CLARIFICATION_REQUIRED'),
                ('Conseille-moi sur la Perceuse Inconnue V017.', 'NO_RESULTS')]:
                answer = ask(customer, question, 'unknown')
                assert answer['advice_status'] == expected and not answer['recommendations']
            record('B_C_unknown_and_ambiguous')
            raised = ask(customer, 'Et si je monte le budget à 150 euros ?')
            assert raised['budget'] == 150 and raised['category'] == 'DRILL'
            assert raised['last_advice_criteria']['available'] is True
            two = ask(customer, 'Montre-moi seulement deux produits.')
            assert [p['sku'] for p in two['recommendations']] == ['D-A','D-B']
            cheapest = ask(customer, 'Et parmi ceux-là, lequel est le moins cher ?')
            assert [p['sku'] for p in cheapest['recommendations']] == ['D-A']
            record('D_budget_count_cheapest')
            for client, thread in [(customer,'new'),(other,'advice')]:
                isolated = ask(client, 'Compare cette perceuse avec deux autres.', thread)
                assert isolated['advice_status'] == 'CLARIFICATION_REQUIRED' and not isolated['recommendations']
            record('D_customer_thread_isolation')
            after_advice = snapshot()
            assert before['product_fingerprint'] == after_advice['product_fingerprint']
            assert before['counts'] == after_advice['counts']
            record('advice_read_only', before_counts=before['counts'], after_counts=after_advice['counts'])
            stock = ask(customer, 'Le stock du SKU D-C ?', 'order')
            assert stock['intent'] == 'STOCK_CHECK' and not stock.get('recommendations')
            prepared = ask(customer, 'Commande-moi 2 SKU D-C', 'order')
            assert prepared['order_status'] == 'PENDING_CONFIRMATION'
            assert prepared['pending_order']['product_sku'] == 'D-C'
            assert snapshot()['product_fingerprint'] == before['product_fingerprint'], 'P0: premature stock mutation'
            confirmed = ask(customer, 'Oui je confirme', 'order')
            assert confirmed['order_status'] == 'CONFIRMED'
            order_id = confirmed['created_order_id']
            current = snapshot()
            assert next(p for p in current['products'] if p['sku']=='D-C')['stock'] == 3
            assert current['counts']['orders'] == before['counts']['orders'] + 1
            replay = ask(customer, 'Oui je confirme', 'order')
            assert replay['created_order_id'] == order_id
            assert snapshot()['product_fingerprint'] == current['product_fingerprint'], 'P0: duplicate stock reservation'
            status = ask(customer, 'Quel est le statut de ma dernière commande ?', 'order')
            assert status['queried_order_id'] == order_id and status['queried_order_status'] == 'CREATED'
            assert customer.get('/orders/' + order_id).status_code == 200
            assert other.get('/orders/' + order_id).status_code == 404
            cancelled = customer.post('/customer/orders/' + order_id + '/cancel')
            assert cancelled.status_code == 200 and cancelled.json()['status'] == 'CANCELLED'
            assert snapshot()['product_fingerprint'] == before['product_fingerprint'], 'P0: cancellation stock mismatch'
            assert customer.post('/customer/orders/' + order_id + '/cancel').status_code == 200
            assert snapshot()['product_fingerprint'] == before['product_fingerprint']
            record('E_order_prepare_confirm_atomic_replay_status_cancel', order_id=order_id,
                   stock_before=5, stock_after_preparation=5, stock_after_confirmation=3, stock_after_replay=3, stock_after_cancellation=5)
            for question in ['Commande-moi -1 SKU D-C','Commande-moi 1,5 SKU D-C','Commande-moi 0 SKU D-C',
                             'Commande-moi 2 Perceuse Pro V017']:
                refused = ask(customer, question, 'invalid-order')
                assert refused.get('order_status') != 'PENDING_CONFIRMATION'
                assert snapshot()['product_fingerprint'] == before['product_fingerprint']
            record('E_invalid_quantities_ambiguous_reference')
            mode_path = DEFAULT / 'control/mode'
            for mode in ('offline','invalid'):
                mode_path.write_text(mode + '\n')
                try:
                    degraded = ask(customer, 'Je cherche une perceuse disponible à moins de 100 euros.', 'degraded-' + mode)
                    assert [p['sku'] for p in degraded['recommendations']] == ['D-A','D-B','D-C']
                    assert 'Présentation déterministe' in degraded['response']
                    verify_cards(degraded)
                    record('F_llm_' + mode)
                finally:
                    mode_path.write_text('normal\n')
            for label, kwargs, expected in [
                ('expired_jwt', {'headers': {'Authorization':'Bearer '+token(lifetime=-60)}},401),
                ('invalid_jwt', {'headers': {'Authorization':'Bearer invalid'}},401)]:
                response=customer.post('/assistant',json={'message':'Bonjour','thread_id':prefix},**kwargs)
                assert response.status_code == expected
                record('F_' + label,http_status=response.status_code)
            large=customer.post('/assistant',json={'message':'a'*140000,'thread_id':prefix})
            assert large.status_code == 413
            record('F_excessive_payload',http_status=large.status_code)
            assert customer.get('/products',headers={'Host':'untrusted.example'}).status_code == 400
            assert customer.get('/products',headers={'Origin':'https://untrusted.example'}).status_code == 403
            record('security_host_origin')
            # A session is persisted independently of the API process.
            with httpx.Client(base_url=manifest['url']+'/api',verify=ssl.create_default_context(cafile=str(DEFAULT/'tls/server.crt')),
                              headers={'Origin':manifest['url']},timeout=10) as admin:
                secret=credentials()
                login=admin.post('/backoffice/session/login',headers={'X-Admin-CSRF':'login'},
                    json={'username':secret['admin_username'],'password':secret['admin_password']})
                assert login.status_code == 200
                cookie=login.headers['set-cookie']
                assert all(flag in cookie.lower() for flag in ('secure','httponly','samesite=strict'))
                assert admin.get('/backoffice/dashboard').status_code == 200
                assert admin.patch('/backoffice/products/D-C',json={'price':49.9}).status_code == 403
                # Reconnect a real PostgreSQL checkpoint and Mongo-backed admin session after restart.
                compose(DEFAULT,'restart','api',stdout=__import__('subprocess').DEVNULL,stderr=__import__('subprocess').DEVNULL)
                for _ in range(60):
                    try:
                        if admin.get('/health').status_code==200: break
                    except httpx.HTTPError: pass
                    time.sleep(.5)
                assert admin.get('/backoffice/session/me').status_code == 200
                continued=ask(customer,'Et si je monte le budget à 150 euros ?')
                assert continued['budget']==150 and continued['category']=='DRILL'
                assert continued['last_advice_criteria']['available'] is True
                record('postgres_checkpoint_and_admin_session_survive_api_restart')
                csrf=login.json()['csrf_token']
                assert admin.post('/backoffice/session/logout',headers={'X-Admin-CSRF':csrf}).status_code==204
                assert admin.get('/backoffice/session/me').status_code==401
                record('security_admin_session_csrf_logout')
            record('final_candidate_integrity', snapshot=snapshot())
    except Exception as error:
        record('validation_interrupted',status='FAIL',error_type=type(error).__name__,detail=str(error)[:200])
        raise


if __name__ == '__main__':
    main()
