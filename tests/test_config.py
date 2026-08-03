import pytest
import yaml

from checker.config import ConfigError, parse_config


def load(text: str):
    return parse_config(yaml.safe_load(text))


def test_defaults_are_inherited_and_overridable():
    config = load(
        """
        defaults:
          timeout: 30
          max_items: 10
          exclude: ["/tag/"]
        sites:
          - name: A
            url: https://a.example/news/
          - name: B
            url: https://b.example/news/
            timeout: 5
            exclude: ["/x/", "/y/"]
        """
    )
    a, b = config.sites
    assert (a.timeout, a.max_items, a.exclude) == (30, 10, ("/tag/",))
    assert (b.timeout, b.max_items, b.exclude) == (5, 10, ("/x/", "/y/"))


def test_id_is_derived_from_name_when_omitted():
    config = load(
        """
        sites:
          - name: 総務省 報道資料
            url: https://a.example/news/
          - id: kept
            name: B
            url: https://b.example/
        """
    )
    # 日本語だけの名前は英数字が残らないのでフォールバックされる。
    assert config.sites[0].id == "site"
    assert config.sites[1].id == "kept"


def test_scalar_string_is_accepted_where_a_list_is_expected():
    config = load(
        """
        sites:
          - name: A
            url: https://a.example/
            include: /news/
        """
    )
    assert config.sites[0].include == ("/news/",)


def test_enabled_sites_filters_and_validates_only():
    config = load(
        """
        sites:
          - id: a
            url: https://a.example/
          - id: b
            url: https://b.example/
            enabled: false
        """
    )
    assert [s.id for s in config.enabled_sites()] == ["a"]
    assert [s.id for s in config.enabled_sites(["a"])] == ["a"]
    # 無効化されたサイトを名指ししても巡回対象にはならない。
    assert config.enabled_sites(["b"]) == []
    with pytest.raises(ConfigError):
        config.enabled_sites(["zzz"])


@pytest.mark.parametrize(
    "text",
    [
        "sites: []",
        "defaults: {}",
        "sites:\n  - name: A",
        "sites:\n  - url: ftp://a.example/",
        "sites:\n  - {id: a, url: 'https://a.example/'}\n  - {id: a, url: 'https://b.example/'}",
    ],
)
def test_invalid_configs_are_rejected(text):
    with pytest.raises(ConfigError):
        load(text)
