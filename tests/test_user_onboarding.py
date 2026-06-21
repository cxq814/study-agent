"""测试用户自助注册流程。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from src.storage.sqlite_client import get_user, upsert_user
from src.tools.profile_tools import validate_profile, build_user_updates


def test_validate_profile_empty_name():
    ok, err = validate_profile("", "计算机科学与技术")
    assert not ok
    assert "姓名" in err


def test_validate_profile_empty_major():
    ok, err = validate_profile("张三", "")
    assert not ok
    assert "主修" in err


def test_validate_profile_ok():
    ok, err = validate_profile("张三", "计算机科学与技术")
    assert ok
    assert err is None


def test_build_user_updates():
    existing = {"user_id": "u001", "grade": "2022级"}
    result = build_user_updates(
        student_name="李四", major="金融学",
        grade="2024级", interests_str="金融,数据科学",
        existing=existing,
    )
    assert result["student_name"] == "李四"
    assert result["major"] == "金融学"
    assert result["grade"] == "2024级"
    assert result["has_minor"] == 0  # preserved from existing


def test_upsert_and_read_user():
    upsert_user(
        user_id="test_onboard",
        student_name="测试用户",
        major="测试专业",
        grade="2024级",
        interests='["金融","数据科学"]',
        has_minor=0,
    )
    u = get_user("test_onboard")
    assert u is not None
    assert u["student_name"] == "测试用户"
    assert u["major"] == "测试专业"
