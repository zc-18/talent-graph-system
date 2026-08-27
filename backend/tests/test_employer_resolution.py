from app.services.employer_resolution import (
    employer_independence_key,
    normalize_employer_name,
    resolve_employer_name,
    stable_employer_id,
)


def test_legal_suffix_and_recruitment_qualifier_normalize_to_same_entity():
    assert normalize_employer_name("腾讯科技（深圳）有限公司") == "腾讯科技"
    assert normalize_employer_name("腾讯科技有限公司") == "腾讯科技"
    assert stable_employer_id("腾讯科技（深圳）有限公司") == stable_employer_id("腾讯科技有限公司")


def test_unknown_employer_has_no_stable_identity():
    assert stable_employer_id("未知") is None
    assert employer_independence_key({"platform": "国聘"}) is None


def test_known_parent_group_is_independence_unit():
    assert employer_independence_key({"employer_id": 21, "employer_parent_id": 3}) == "3"
    assert employer_independence_key({"employer_id": 22, "employer_parent_id": 3}) == "3"


def test_reviewed_brand_alias_resolves_to_canonical_employer():
    aliases = {"腾讯": "腾讯科技（深圳）有限公司"}
    assert resolve_employer_name("腾讯", aliases) == "腾讯科技（深圳）有限公司"
    assert stable_employer_id("腾讯", aliases) == stable_employer_id("腾讯科技有限公司")
