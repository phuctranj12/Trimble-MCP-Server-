import pytest

from app.trimble.errors import ErrorCode, TrimbleError
from tests.conftest import connect, token_response


async def test_list_projects_follows_pagination(services):
    connect(services, "user-a", "tok-a")
    projects, errors = await services.trimble_for("user-a").list_projects()
    assert [p["id"] for p in projects] == ["pA1", "pA2"]
    assert errors == []
    assert services.repo.get_project_region("user-a", "pA2") == "northAmerica"


async def test_user_cannot_read_other_users_project(services):
    connect(services, "user-a", "tok-a")
    connect(services, "user-b", "tok-b")
    a, b = services.trimble_for("user-a"), services.trimble_for("user-b")

    project, _ = await b.get_project("pB1")
    assert project["name"] == "Project B1"

    with pytest.raises(TrimbleError) as ei:
        await a.get_project("pB1")
    assert ei.value.code == ErrorCode.NOT_FOUND


async def test_region_mapping_is_per_user(services):
    connect(services, "user-a", "tok-a")
    connect(services, "user-b", "tok-b")
    await services.trimble_for("user-b").list_projects()
    assert services.repo.get_project_region("user-a", "pB1") is None


async def test_401_triggers_one_refresh_then_retries(services, fake):
    connect(services, "user-a", "tok-a")
    fake.fail_next_401 = True
    fake.token_responses.append(token_response("tok-a2"))
    project, _ = await services.trimble_for("user-a").get_project("pA1")
    assert project["id"] == "pA1"
    assert services.repo.get_tokens("user-a").access_token == "tok-a2"


async def test_not_connected(services):
    services.repo.upsert_user("user-c", None, None)
    with pytest.raises(TrimbleError) as ei:
        await services.trimble_for("user-c").list_projects()
    assert ei.value.code == ErrorCode.NOT_CONNECTED


async def test_topics_and_folder(services):
    connect(services, "user-a", "tok-a")
    c = services.trimble_for("user-a")
    page, region = await c.list_topics("pA1")
    assert len(page.items) == 2 and region.topics_base == "https://open11.connect.trimble.com"
    items, folder, _ = await c.list_folder_items("pA1", None)
    assert folder == "fA1" and len(items.items) == 2
