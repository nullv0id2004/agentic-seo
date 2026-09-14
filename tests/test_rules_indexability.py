from rules.indexability import (
    evaluate_critical_rules,
    looks_indexable,
    path_of,
    rule_matches,
    schema_price_mismatches,
)

KORUM_RULES = [
    {"rule_key": "no_indexable_auth_route", "url_pattern": r"^/(dashboard|recruiter|admin|api)/", "assertion": "must_noindex"},
    {"rule_key": "auth_routes_absent_from_sitemap", "url_pattern": r"^/(dashboard|recruiter|admin)/", "assertion": "must_not_appear_in_sitemap"},
    {"rule_key": "no_jobposting_schema", "url_pattern": ".*", "assertion": "schema_type_forbidden:JobPosting"},
]


def test_path_matching_ignores_host():
    assert path_of("https://korum.worldhire.com/recruiter/inbox?x=1") == "/recruiter/inbox?x=1"
    assert rule_matches(r"^/(dashboard|recruiter)/", "https://korum.worldhire.com/recruiter/inbox")
    assert not rule_matches(r"^/(dashboard|recruiter)/", "https://korum.worldhire.com/blog/recruiter-tips")


def test_looks_indexable():
    assert looks_indexable(200, None, None)
    assert not looks_indexable(200, "noindex", None)
    assert not looks_indexable(200, None, "noindex, nofollow")
    assert not looks_indexable(401, None, None)
    assert not looks_indexable(302, None, None)
    assert looks_indexable(None, None, None), "unknown must not read as safe"


def test_leaked_auth_route_is_a_violation():
    pages = [{"url": "https://korum.worldhire.com/recruiter/inbox", "status_code": 200, "robots_meta": None, "x_robots_tag": None}]
    v = evaluate_critical_rules(KORUM_RULES, pages)
    assert [x.rule_key for x in v] == ["no_indexable_auth_route"]


def test_protected_route_is_clean():
    pages = [{"url": "https://korum.worldhire.com/recruiter/inbox", "status_code": 200, "robots_meta": None, "x_robots_tag": "noindex"}]
    assert evaluate_critical_rules(KORUM_RULES, pages) == []


def test_sitemap_membership_and_forbidden_schema():
    pages = [{"url": "https://korum.worldhire.com/jobs/1", "status_code": 200, "schema_types": ["JobPosting", "Organization"]}]
    v = evaluate_critical_rules(KORUM_RULES, pages, sitemap_urls={"https://korum.worldhire.com/dashboard/home"})
    keys = sorted(x.rule_key for x in v)
    assert keys == ["auth_routes_absent_from_sitemap", "no_jobposting_schema"]


def test_variant_canonical_and_price_mismatch():
    rules = [
        {"rule_key": "variant_canonical", "url_pattern": r"^/products/.*\?variant=", "assertion": "must_canonical_to_parent"},
        {"rule_key": "product_price_matches_schema", "url_pattern": r"^/products/", "assertion": "schema_matches_visible"},
    ]
    good = {"url": "https://rejuveluxe.com/products/serum?variant=30ml", "canonical": "https://rejuveluxe.com/products/serum",
            "visible_price": "$49.00", "raw_jsonld": [{"@type": "Product", "offers": {"@type": "Offer", "price": "49.00"}}]}
    bad = {"url": "https://rejuveluxe.com/products/serum?variant=50ml", "canonical": "https://rejuveluxe.com/products/serum?variant=50ml",
           "visible_price": "$59.00", "raw_jsonld": [{"@type": "Product", "offers": {"@type": "Offer", "price": "49.00"}}]}
    assert evaluate_critical_rules(rules, [good]) == []
    keys = sorted(x.rule_key for x in evaluate_critical_rules(rules, [bad]))
    assert keys == ["product_price_matches_schema", "variant_canonical"]
    assert schema_price_mismatches({"visible_price": "₹1,299", "raw_jsonld": [{"@type": "Offer", "price": 1299}]}) == []
