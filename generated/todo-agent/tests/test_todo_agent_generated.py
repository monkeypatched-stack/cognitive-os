import pytest
from todo import TodoAgent  # Assuming the TodoAgent class is in todo.py


@pytest.fixture
def todo_agent():
    """Provides a fixture for the TodoAgent instance."""
    global todo_agent  # Use global to make it accessible from tests
    todo_agent = TodoAgent()
    yield todo_agent
    # Clean up after each test run (optional but recommended)
    # todo_agent.cleanup()


def test_create_new_todos():
    """Test that users can create new todos."""
    title = "Buy groceries"
    description = "Milk, eggs, bread, and cheese"

    try:
        todo_agent.create_todo(title=title, description=description)
        assert todo_agent.list_todos()  # Verify the todo was created
        created_todo = todo_agent.get_todo(
            todo_agent.last_todo_id()
        )  # Retrieve the newly created todo
        assert created_todo.title == title
        assert created_todo.description == description

    finally:
        pass  # Clean up after test, regardless of outcome


def test_mark_todos_complete():
    """Test that users can mark todos as complete."""
    title = "Pay bills"
    try:
        todo_agent.create_todo(title=title)
        todo_agent.mark_complete(title=title)
        assert todo_agent.list_todos()  # Verify the todo still exists
        completed_todo = todo_agent.get_todo(
            todo_agent.last_todo_id()
        )  # Retrieve the newly created todo
        assert completed_todo.completed == True

    finally:
        todo_agent.delete_todo(title=title)


def test_list_todos():
    """Test that users can list todos."""
    title1 = "Walk dog"
    title2 = "Do laundry"
    description = "Wash clothes, dry them."

    try:
        todo_agent.create_todo(title=title1, description=description)
        todo_agent.create_todo(title=title2)
        todos = todo_agent.list_todos()
        assert len(todos) == 2
        for todo in todos:
            assert (
                todo.completed == False
            )  # ensure that all the lists are not marked as complete

    finally:
        todo_agent.delete_all_todos()


def test_delete_todos():
    """Test that users can delete todos."""
    title = "Clean room"
    try:
        todo_agent.create_todo(title=title)
        todo_agent.delete_todo(title=title)
        assert todo_agent.list_todos() == []  # Verify the todo was deleted
    finally:
        pass


def test_persistence():
    """Test that todos are persisted to a database."""
    # This test relies on the TodoAgent's implementation for persistence.
    # Add assertions to verify the data is stored correctly after creating, updating, and deleting todos.
    # Example (adjust based on your actual TodoAgent's storage mechanism):

    title = "Test todo"
    todo_agent.create_todo(title=title)
    todos = todo_agent.list_todos()
    assert len(todos) == 1
    assert todos[0].title == title

    # Reset the todo list to ensure a clean state for subsequent tests
    todo_agent.delete_all_todos()
