# Test suites

The default suite is offline and must not contact operational services:

```powershell
python -m pip install -r requirements-dev.txt
pytest -m "not postgres and not live"
```

PostgreSQL integration tests require a disposable database with the project
migrations already applied:

```powershell
$env:TEST_DATABASE_URL = "postgresql://user:password@localhost/wechat_bot_test"
pytest -m postgres
```

`TEST_DATABASE_URL` must never point to production. The test configuration
rejects the known production host and does not provide a bypass.

Tests marked `live` may contact an external provider. They are excluded from
normal local and CI runs and must always require an additional explicit opt-in
specific to that provider.
