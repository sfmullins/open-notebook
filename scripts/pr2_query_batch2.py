#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def rw(path, fn):
    p = ROOT / path
    old = p.read_text(encoding='utf-8')
    new = fn(old)
    if new == old:
        raise RuntimeError(f'no change made to {path}')
    p.write_text(new, encoding='utf-8')

def one(text, old, new, label):
    if text.count(old) != 1:
        raise RuntimeError(f'{label}: expected one match, got {text.count(old)}')
    return text.replace(old, new, 1)

# config health
rw('api/routers/config.py', lambda t: one(
    one(t, 'from open_notebook.database.repository import repo_query\n',
        'from open_notebook.database.repository import repo_healthcheck\n', 'config import'),
    'result = await asyncio.wait_for(repo_query("RETURN 1"), timeout=2.0)\n        if result:\n',
    'result = await asyncio.wait_for(repo_healthcheck(), timeout=2.0)\n        if result:\n', 'config health'))

# podcast models

def podcasts(t):
    t = one(t, 'from open_notebook.database.repository import ensure_record_id, repo_query\n',
        'from open_notebook.database.repository import (\n    ensure_record_id,\n    repo_command_rows,\n    repo_get,\n    repo_list,\n)\n', 'podcast import')
    t = one(t, '''        result = await repo_query(\n            "SELECT * FROM episode_profile WHERE name = $name", {"name": name}\n        )\n''',
        '''        result = await repo_list("episode_profile", filters={"name": name}, limit=1)\n''', 'episode by name')
    t = one(t, '''        result = await repo_query(\n            "SELECT * FROM speaker_profile WHERE name = $name", {"name": name}\n        )\n''',
        '''        result = await repo_list("speaker_profile", filters={"name": name}, limit=1)\n''', 'speaker by name')
    t = one(t, '''            result = await repo_query(\n                "SELECT * FROM $id", {"id": ensure_record_id(ref_str)}\n            )\n            if result:\n                return cls(**result[0])\n''',
        '''            result = await repo_get(ref_str)\n            if result:\n                return cls(**result)\n''', 'speaker resolve')
    start = t.find('        try:\n            result = await repo_query(\n                "SELECT * FROM command WHERE id IN $command_ids",')
    if start < 0: raise RuntimeError('command batch start missing')
    end = t.find('        except Exception as e:', start)
    if end < 0: raise RuntimeError('command batch end missing')
    repl = '''        try:\n            result = await repo_command_rows(ids)\n'''
    t = t[:start] + repl + t[end:]
    t = t.replace('same SURREAL_* env vars', 'same PostgreSQL database')
    return t
rw('open_notebook/podcasts/models.py', podcasts)

# credential deletion fallback

def credentials(t):
    t = one(t,
        'from open_notebook.database.repository import ensure_record_id, repo_delete, repo_query\n',
        'from open_notebook.database.repository import (\n    repo_delete,\n    repo_list,\n    repo_update_record,\n)\n', 'credential imports')
    t = one(t, '''            linked = await repo_query(\n                "SELECT * FROM model WHERE credential = $cred_id",\n                {"cred_id": ensure_record_id(credential_id)},\n            )\n''',
        '''            linked = await repo_list("model", filters={"credential": credential_id})\n''', 'credential linked')
    pattern = re.compile(r'''\s+await repo_query\(\n\s+"UPDATE \$model_id SET credential = \$target_id",\n\s+\{\n\s+"model_id": ensure_record_id\(model_id\),\n\s+# A fetched credential always has an id; fall\n\s+# back to the requested id for the type checker\.\n\s+"target_id": ensure_record_id\(\n\s+target_cred\.id or migrate_to\n\s+\),\n\s+\},\n\s+\)''')
    t, n = pattern.subn('\n                        await repo_update_record(\n                            model_id, {"credential": target_cred.id or migrate_to}\n                        )', t, count=1)
    if n != 1: raise RuntimeError(f'credential update replaced {n}')
    return t
rw('api/routers/credentials.py', credentials)

# models router duplicate/list queries

def models(t):
    t = one(t, '        from open_notebook.database.repository import repo_query\n\n        existing = await repo_query(\n            "SELECT * FROM model WHERE string::lowercase(provider) = $provider AND string::lowercase(name) = $name AND string::lowercase(type) = $type LIMIT 1",\n            {\n                "provider": model_data.provider.lower(),\n                "name": model_data.name.lower(),\n                "type": model_data.type.lower(),\n            },\n        )\n',
        '        from open_notebook.database.repository import repo_list\n\n        existing = await repo_list(\n            "model",\n            filters={\n                "provider": model_data.provider,\n                "name": model_data.name,\n                "type": model_data.type,\n            },\n            case_insensitive_fields={"provider", "name", "type"},\n            limit=1,\n        )\n', 'model duplicate')
    t = one(t, '        from open_notebook.database.repository import repo_query\n\n        models = await repo_query(\n            "SELECT * FROM model WHERE provider = $provider ORDER BY type, name",\n            {"provider": provider},\n        )\n',
        '        from open_notebook.database.repository import repo_list\n\n        models = await repo_list("model", filters={"provider": provider}, order_by="type")\n        models.sort(key=lambda row: (row.get("type", ""), row.get("name", "")))\n', 'models by provider')
    t = one(t, '        from open_notebook.database.repository import repo_query\n\n        # Get current defaults\n',
        '        from open_notebook.database.repository import repo_list\n\n        # Get current defaults\n', 'auto assign import')
    t = one(t, '        all_models = await repo_query(\n            "SELECT * FROM model ORDER BY provider, name",\n            {},\n        )\n',
        '        all_models = await repo_list("model")\n        all_models.sort(key=lambda row: (row.get("provider", ""), row.get("name", "")))\n', 'all models')
    return t
rw('api/routers/models.py', models)

# credential service model lookup queries

def cred_service(t):
    t = t.replace('from open_notebook.database.repository import repo_query', 'from open_notebook.database.repository import repo_list')
    t = one(t, '''    existing_models = await repo_query(\n        "SELECT string::lowercase(name) as name, string::lowercase(type) as type FROM model "\n        "WHERE string::lowercase(provider) = $provider",\n        {"provider": cred.provider.lower()},\n    )\n    existing_keys = {(m["name"], m["type"]) for m in existing_models}\n''',
        '''    existing_models = await repo_list(\n        "model",\n        filters={"provider": cred.provider},\n        case_insensitive_fields={"provider"},\n    )\n    existing_keys = {(str(m.get("name", "")).lower(), str(m.get("type", "")).lower()) for m in existing_models}\n''', 'register existing')
    old = '''                provider_models = await repo_query(\n                    "SELECT * FROM model WHERE string::lowercase(provider) = $provider AND credential IS NONE",\n                    {"provider": provider.lower()},\n                )'''
    new = '''                provider_models = await repo_list(\n                    "model",\n                    filters={"provider": provider},\n                    case_insensitive_fields={"provider"},\n                    non_null_fields=(),\n                )\n                provider_models = [row for row in provider_models if row.get("credential") is None]'''
    if t.count(old) != 1: raise RuntimeError(f'provider migration matches {t.count(old)}')
    t = t.replace(old, new, 1)
    old2 = '''            provider_models = await repo_query(\n                "SELECT * FROM model WHERE string::lowercase(provider) = $provider AND credential IS NONE",\n                {"provider": provider.lower()},\n            )'''
    new2 = '''            provider_models = await repo_list(\n                "model",\n                filters={"provider": provider},\n                case_insensitive_fields={"provider"},\n            )\n            provider_models = [row for row in provider_models if row.get("credential") is None]'''
    if t.count(old2) != 1: raise RuntimeError(f'env migration matches {t.count(old2)}')
    t = t.replace(old2, new2, 1)
    return t
rw('api/credentials_service.py', cred_service)

# podcast command profile lists

def podcast_commands(t):
    t = one(t, 'from open_notebook.database.repository import ensure_record_id, repo_query\n',
        'from open_notebook.database.repository import ensure_record_id, repo_list\n', 'podcast command import')
    t = one(t, '        episode_profiles = await repo_query("SELECT * FROM episode_profile")\n        speaker_profiles = await repo_query("SELECT * FROM speaker_profile")\n',
        '        episode_profiles = await repo_list("episode_profile")\n        speaker_profiles = await repo_list("speaker_profile")\n', 'podcast profile lists')
    return t
rw('commands/podcast_commands.py', podcast_commands)

for rel in ('scripts/pr2_query_batch2.py', '.github/workflows/pr2-query-batch2.yml'):
    p = ROOT / rel
    if p.exists(): p.unlink()
