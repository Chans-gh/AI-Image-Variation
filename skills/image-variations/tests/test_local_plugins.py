"""本地私有扩展机制：加载/合并/隔离，缺目录正常、坏插件必须显式报错。

本套件验证"公开仓库不带任何真实平台接入"这条边界：
- 没有 `local/` 时一切正常（公开仓库与 CI 的实际状态）
- 有 `local/` 时按约定加载（供应商插件、能力档案、配置覆盖、本地测试）
- 插件损坏、档案缺段一律显式报错（BUILTIN_UNAVAILABLE / INVALID_INPUT），不静默跳过
- **注入配置/档案时不合并本地覆盖**，保证测试可重复

运行：.venv/bin/python skills/image-variations/tests/test_local_plugins.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SCRIPTS = HERE.parent / "scripts"
for entry in (str(REPO), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from iv_skill import local_plugins as lp  # noqa: E402
from shared.errors import IVError  # noqa: E402

PLUGIN_OK = textwrap.dedent('''
    class DummyAdapter:
        provider_type = "dummy_platform_compatible"

    def register(registry):
        registry[DummyAdapter.provider_type] = DummyAdapter
''')

PLUGIN_BROKEN = "import definitely_not_a_real_module_xyz\n"

PLUGIN_NO_ENTRY = "X = 1\n"


class LocalRootCase(unittest.TestCase):
    """把 IV_LOCAL_ROOT 指到临时目录，隔离真实 local/。"""

    def setUp(self):
        self._old = os.environ.get(lp.LOCAL_ROOT_ENV)
        self.tmp = tempfile.TemporaryDirectory()
        os.environ[lp.LOCAL_ROOT_ENV] = self.tmp.name
        lp.reset_cache()
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop(lp.LOCAL_ROOT_ENV, None)
        else:
            os.environ[lp.LOCAL_ROOT_ENV] = self._old
        lp.reset_cache()
        self.tmp.cleanup()

    def write(self, rel: str, content: str) -> Path:
        path = Path(self.tmp.name) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


class TestAbsentLocalDir(LocalRootCase):
    def test_no_local_dir_is_fine(self):
        self.assertEqual(lp.iter_provider_modules(), [])
        self.assertEqual(lp.profiles_paths(), [])
        self.assertEqual(lp.config_override_paths(), [])
        self.assertEqual(lp.local_tests_dirs(), [])
        self.assertEqual(lp.register_providers({}), [])

    def test_status_reports_absence(self):
        """目录不存在才算"没有本地扩展"；空目录与不存在都要能正常工作。"""
        os.environ[lp.LOCAL_ROOT_ENV] = str(Path(self.tmp.name) / "not-created-yet")
        self.assertFalse(lp.status()["present"])
        self.assertEqual(lp.iter_provider_modules(), [])
        self.assertEqual(lp.register_providers({}), [])


class TestPluginLoading(LocalRootCase):
    def test_flat_layout_is_loaded(self):
        self.write("providers/dummy_compat.py", PLUGIN_OK)
        registry: dict = {}
        self.assertEqual(lp.register_providers(registry), ["dummy_platform_compatible"])
        self.assertIn("dummy_platform_compatible", registry)

    def test_platform_subdir_layout_is_loaded(self):
        self.write("someplatform/providers/dummy_compat.py", PLUGIN_OK)
        registry: dict = {}
        self.assertEqual(lp.register_providers(registry), ["dummy_platform_compatible"])

    def test_underscore_files_are_skipped(self):
        self.write("providers/_helper.py", PLUGIN_BROKEN)
        self.assertEqual(lp.register_providers({}), [])

    def test_broken_plugin_fails_loudly(self):
        self.write("providers/dummy_compat.py", PLUGIN_BROKEN)
        with self.assertRaises(IVError) as ctx:
            lp.register_providers({})
        self.assertEqual(ctx.exception.code, "BUILTIN_UNAVAILABLE")
        self.assertIn("插件加载失败", ctx.exception.message)

    def test_plugin_without_entry_point_fails_loudly(self):
        self.write("providers/dummy_compat.py", PLUGIN_NO_ENTRY)
        with self.assertRaises(IVError) as ctx:
            lp.register_providers({})
        self.assertEqual(ctx.exception.code, "BUILTIN_UNAVAILABLE")
        self.assertIn("register(registry)", ctx.exception.message)

    def test_second_call_is_cached(self):
        self.write("providers/dummy_compat.py", PLUGIN_OK)
        registry: dict = {}
        lp.register_providers(registry)
        self.assertEqual(lp.register_providers(registry), [], "重复调用不应重复导入")


class TestProfileAndConfigMerge(LocalRootCase):
    def test_local_profiles_override_base(self):
        self.write("platform/provider-profiles.json", json.dumps({
            "profiles": {"a": {"model_id": "local-a"}, "b": {"model_id": "local-b"}}}))
        merged = lp.merge_profiles({"profiles": {"a": {"model_id": "base-a"},
                                                 "c": {"model_id": "base-c"}}})
        self.assertEqual(merged["profiles"]["a"]["model_id"], "local-a")
        self.assertEqual(merged["profiles"]["b"]["model_id"], "local-b")
        self.assertEqual(merged["profiles"]["c"]["model_id"], "base-c")
        self.assertEqual(len(merged["local_profiles_sources"]), 1)

    def test_profiles_without_section_fails_loudly(self):
        self.write("provider-profiles.json", json.dumps({"profiles_typo": {}}))
        with self.assertRaises(IVError) as ctx:
            lp.merge_profiles({"profiles": {}})
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_broken_json_fails_loudly(self):
        self.write("provider-profiles.json", "{not json")
        with self.assertRaises(IVError) as ctx:
            lp.merge_profiles({"profiles": {}})
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_config_deep_merge_keeps_untouched_keys(self):
        self.write("iv.config.local.json", json.dumps(
            {"seam": {"grade_mode": "continuity_gate"}, "extra_section": {"k": 1}}))
        base = {"seam": {"grade_mode": "artifact_only", "band_px": 6}, "tile_edge_min_px": 1024}
        merged = lp.merge_config(base)
        self.assertEqual(merged["seam"]["grade_mode"], "continuity_gate")
        self.assertEqual(merged["seam"]["band_px"], 6, "未覆盖的键必须保留")
        self.assertEqual(merged["tile_edge_min_px"], 1024)
        self.assertEqual(merged["extra_section"], {"k": 1})

    def test_local_tests_dir_discovered(self):
        self.write("platform/tests/test_x.py", "x = 1\n")
        self.assertEqual([p.name for p in lp.local_tests_dirs()], ["tests"])


class TestInjectedConfigIsIsolated(LocalRootCase):
    """注入配置/档案时必须跳过本地覆盖，否则测试结果会被本机环境影响。"""

    def test_injected_config_skips_local_override(self):
        from iv_skill.config import load_config

        self.write("iv.config.local.json", json.dumps({"tile_edge_min_px": 1}))
        injected = json.loads((HERE.parent / "config" / "iv.config.json").read_text(encoding="utf-8"))
        path = self.write("injected.json", json.dumps(injected))
        self.assertEqual(load_config(path)["tile_edge_min_px"],
                         injected["tile_edge_min_px"])

    def test_injected_profiles_skip_local_merge(self):
        from iv_skill.config import load_provider_profiles

        self.write("provider-profiles.json", json.dumps({"profiles": {"local": {}}}))
        path = self.write("injected-profiles.json", json.dumps({"profiles": {}}))
        self.assertEqual(load_provider_profiles(path)["profiles"], {})

    def test_default_config_picks_up_local_override(self):
        from iv_skill.config import load_config

        self.write("iv.config.local.json", json.dumps({"tile_edge_warn_px": 999}))
        old_config_env = os.environ.pop("IV_CONFIG", None)
        self.addCleanup(lambda: os.environ.update({"IV_CONFIG": old_config_env})
                        if old_config_env else os.environ.pop("IV_CONFIG", None))
        self.assertEqual(load_config()["tile_edge_warn_px"], 999)


class TestRegistryIntegration(LocalRootCase):
    def test_unknown_provider_type_points_at_local_dir(self):
        from iv_skill.provider import registry

        old = registry._LOCAL_PROVIDERS_LOADED
        registry._LOCAL_PROVIDERS_LOADED = False
        self.addCleanup(lambda: setattr(registry, "_LOCAL_PROVIDERS_LOADED", old))
        with self.assertRaises(IVError) as ctx:
            registry.build_adapter(provider_type="definitely_not_a_provider",
                                   capability_declaration={"text_to_image": True})
        self.assertEqual(ctx.exception.code, "MODEL_CAPABILITY_UNSUPPORTED")
        self.assertIn("本地插件目录", ctx.exception.message)

    def test_local_plugin_becomes_available(self):
        from iv_skill.provider import registry

        self.write("providers/dummy_compat.py", PLUGIN_OK)
        old = registry._LOCAL_PROVIDERS_LOADED
        registry._LOCAL_PROVIDERS_LOADED = False
        self.addCleanup(lambda: setattr(registry, "_LOCAL_PROVIDERS_LOADED", old))
        self.assertIn("dummy_platform_compatible", registry.available_provider_types())


if __name__ == "__main__":
    unittest.main(verbosity=2)
