"""Batch mappings keep explicit wire IDs and never replace conflicting choices."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from launcher import display_name, idea_import_models, selected_mappings, suggest_source_id


class BatchMappingTests(unittest.TestCase):
    def test_upstream_declared_name_does_not_prove_its_actual_version(self):
        name, source = display_name("qd/gmodel", {"name": "Upstream Model v99"}, {"gmodel": "IDE reference"}, "qoder_ide")
        self.assertEqual(name, "Upstream Model v99")
        self.assertIn("上游声明名称", source)
        self.assertIn("上游未声明版本；实际版本未核实", source)
        for field in ("version", "model_version"):
            with self.subTest(field=field):
                _, source = display_name("qd/gmodel", {field: "2026.10"}, {}, "qoder_ide")
                self.assertIn("上游声明版本：2026.10（未独立核验）", source)
        _, source = display_name("qd/gmodel", {"version": False}, {}, "qoder_ide")
        self.assertIn("上游未声明版本", source)

    def test_ide_reference_uses_exact_id_or_one_existing_mapping_only(self):
        model = {"id": "qd/gmodel"}
        catalog = {"native/glm": "Current IDE name", "other/glm": "Another name"}
        mapping = {"native/glm": "qd/gmodel"}
        name, source = display_name(model["id"], model, catalog, "qoder_ide", mapping)
        self.assertEqual(name, "Current IDE name")
        self.assertIn("当前 Qoder IDE 目录参考（按已有映射）", source)
        mapping["other/glm"] = model["id"]
        name, source = display_name(model["id"], model, catalog, "qoder_ide", mapping)
        self.assertEqual(name, model["id"])
        self.assertIn("当前 IDE 未提供参考名称", source)
        name, source = display_name(model["id"], model, {**catalog, "gmodel": "Exact ID name"}, "qoder_ide", mapping)
        self.assertEqual(name, "Exact ID name")
        self.assertIn("当前 Qoder IDE 目录参考；", source)

    def test_ide_has_no_builtin_fallback_but_idea_labels_its_references(self):
        model = {"id": "qd/gmodel"}
        name, source = display_name(model["id"], model, {}, "qoder_ide")
        self.assertEqual(name, "qd/gmodel")
        self.assertIn("当前 IDE 未提供参考名称", source)
        name, source = display_name(model["id"], model, {"gmodel": "IDEA current name"})
        self.assertEqual(name, "IDEA current name")
        self.assertIn("本机 IDEA/Qoder 目录参考", source)
        name, source = display_name(model["id"], model, {})
        self.assertEqual(name, "GLM-5.3")
        self.assertIn("内置参考名称", source)
        self.assertIn("实际版本未核实", source)

    def test_ide_import_keeps_the_existing_source_id_with_its_current_reference_name(self):
        models = {"qd/gmodel": {"id": "qd/gmodel"}}
        catalog = {"native/glm": "Changed IDE name"}
        mapping = {"native/glm": "qd/gmodel"}
        result = idea_import_models(models, models, mapping, catalog, catalog, "qoder_ide")
        self.assertEqual(result[0]["source_id"], "native/glm")
        self.assertEqual(result[0]["display_name"], "Changed IDE name")
        self.assertEqual(mapping, {"native/glm": "qd/gmodel"})

    def test_idea_import_preserves_manual_alias_and_only_sends_model_metadata(self):
        models = {"qd/gfmodel": {"id": "qd/gfmodel", "capabilities": {"contextWindow": 200000, "vision": False, "tools": True}}}
        value = idea_import_models(models, models, {"my-glm": "qd/gfmodel"}, {}, {})
        self.assertEqual(value, [{"source_id": "my-glm", "target_id": "qd/gfmodel", "display_name": "GLM-5.3-Flash", "context_length": 200000, "vision": False}])
        self.assertFalse({"key", "api_key", "base_url", "url"}.intersection(value[0]))

    def test_idea_import_requires_an_unambiguous_applied_mapping(self):
        models = {"qd/gfmodel": {"id": "qd/gfmodel"}}
        for mapping in ({}, {"first": "qd/gfmodel", "second": "qd/gfmodel"}):
            with self.assertRaises(ValueError):
                idea_import_models(models, models, mapping, {}, {})
        result = idea_import_models(models, models, {"glm-5.3-flash": "qd/gfmodel", "other": "qd/gfmodel"}, {}, {})
        self.assertEqual(result[0]["source_id"], "glm-5.3-flash")

    def test_multiple_models_and_auto_keep_real_targets(self):
        models = {mid: {"id": mid} for mid in ("qd/gfmodel", "qd/qfmodel", "qd/efficient", "cn-auto")}
        result, conflicts, unchanged = selected_mappings(models, models, {}, {}, {})
        self.assertEqual(result, {"glm-5.3-flash": "qd/gfmodel", "qwen3.8-flash": "qd/qfmodel", "efficient": "qd/efficient", "auto": "cn-auto"})
        self.assertEqual((conflicts, unchanged), ([], 0))

    def test_live_catalog_id_is_used_and_repeat_add_is_idempotent(self):
        models = {"qd/gfmodel": {"id": "qd/gfmodel"}}
        sources = {"z-ai/glm-5.3-flash": "GLM-5.3-Flash"}
        first, _, _ = selected_mappings(models, models, {}, sources, {})
        self.assertEqual(first, {"z-ai/glm-5.3-flash": "qd/gfmodel"})
        second, conflicts, unchanged = selected_mappings(models, models, {}, sources, first)
        self.assertEqual((second, conflicts, unchanged), ({}, [], 1))

    def test_existing_user_mapping_is_never_overwritten(self):
        models = {mid: {"id": mid} for mid in ("qd/gfmodel", "qd/gmodel", "qd/qfmodel")}
        existing = {"glm-5.3-flash": "qd/gmodel"}
        result, conflicts, unchanged = selected_mappings(["qd/gfmodel", "qd/qfmodel"], models, {}, {}, existing)
        self.assertEqual(result, {"qwen3.8-flash": "qd/qfmodel"})
        self.assertEqual(conflicts, ["glm-5.3-flash"])
        self.assertEqual(existing, {"glm-5.3-flash": "qd/gmodel"})

    def test_same_named_selection_conflict_is_order_independent(self):
        models = {mid: {"id": mid, "name": "Shared-Model"} for mid in ("provider-a/model", "provider-b/model")}
        for ids in (list(models), list(reversed(models))):
            self.assertEqual(selected_mappings(ids, models, {}, {}, {}), ({}, ["shared-model"], 0))

    def test_unrecognised_or_ambiguous_label_uses_original_id(self):
        model = {"id": "provider/model-x", "name": "专有模型"}
        self.assertEqual(suggest_source_id(model["id"], model, {}, {}), model["id"])
        model["name"] = "Shared"
        self.assertEqual(suggest_source_id(model["id"], model, {}, {"a/shared": "Shared", "b/shared": "Shared"}), model["id"])

    def test_stale_selection_cannot_add_partial_batch(self):
        models = {"qd/gfmodel": {"id": "qd/gfmodel"}}
        existing = {"saved": "qd/gfmodel"}
        with self.assertRaises(ValueError):
            selected_mappings(["qd/gfmodel", "removed/model"], models, {}, {}, existing)
        self.assertEqual(existing, {"saved": "qd/gfmodel"})


if __name__ == "__main__":
    unittest.main()
