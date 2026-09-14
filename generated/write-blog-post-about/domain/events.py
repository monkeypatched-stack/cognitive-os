from typing import Optional


class WriteBlogPostAboutCreated:
    def __init__(self, blog_post_id: str):
        self.blog_post_id = blog_post_id


class WriteBlogPostAboutUpdated:
    def __init__(self, blog_post_id: str):
        self.blog_post_id = blog_post_id


class WriteBlogPostAboutDeleted:
    def __init__(self, blog_post_id: str):
        self.blog_post_id = blog_post_id
