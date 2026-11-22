from django.db import models

class PublicationSpace(models.Model):
    cache_epoch = models.PositiveBigIntegerField(default=1, db_default=1)
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    default_language = models.CharField(max_length=5, default="en")
    internal_site = models.ForeignKey("backbone.Site", on_delete=models.PROTECT)
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

import uuid


class SpaceRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    space = models.ForeignKey(PublicationSpace, on_delete=models.PROTECT)

    class Meta:
        abstract = True


class ContentEntry(SpaceRecord):
    translation_group_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    entry_type = models.CharField(max_length=4)
    locale = models.CharField(max_length=2)
    status = models.CharField(max_length=12, default="draft")
    working_slug = models.SlugField(max_length=160, allow_unicode=True)
    published_slug = models.SlugField(max_length=160, allow_unicode=True, null=True)
    working_data = models.JSONField(default=dict)
    published_data = models.JSONField(null=True)
    has_unpublished_changes = models.BooleanField(default=True)
    working_in_review = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True)
    revision = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey("auth.User", null=True, on_delete=models.PROTECT, related_name="+")
    updated_by = models.ForeignKey("auth.User", null=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["space","entry_type","locale","working_slug"],name="cms_working_slug"),
            models.UniqueConstraint(fields=["space","entry_type","locale","published_slug"],condition=models.Q(published_slug__isnull=False),name="cms_published_slug"),
            models.UniqueConstraint(fields=["space","entry_type","locale","translation_group_id"],name="cms_translation_locale"),
        ]


class ContentRevision(SpaceRecord):
    entry = models.ForeignKey(ContentEntry,on_delete=models.PROTECT)
    revision_number = models.PositiveIntegerField()
    slug = models.CharField(max_length=160)
    snapshot_data = models.JSONField()
    published_by = models.ForeignKey("auth.User",null=True,on_delete=models.PROTECT)
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=["entry","revision_number"],name="cms_snapshot_revision")]


class SlugRedirect(SpaceRecord):
    entry_type=models.CharField(max_length=4)
    locale=models.CharField(max_length=2)
    old_slug=models.CharField(max_length=160)
    new_slug=models.CharField(max_length=160)
    target_entry=models.ForeignKey(ContentEntry,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=["space","entry_type","locale","old_slug"],name="cms_redirect_slug")]


class NavigationMenu(SpaceRecord):
    location=models.CharField(max_length=6)
    locale=models.CharField(max_length=2)
    revision=models.PositiveIntegerField(default=0)
    updated_at=models.DateTimeField(auto_now=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=["space","location","locale"],name="cms_menu_location")]


class MenuItem(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    menu=models.ForeignKey(NavigationMenu,on_delete=models.CASCADE,related_name="items")
    parent=models.ForeignKey("self",null=True,on_delete=models.CASCADE)
    label=models.CharField(max_length=150)
    target_type=models.CharField(max_length=16)
    target_entry=models.ForeignKey(ContentEntry,null=True,on_delete=models.PROTECT)
    target_path=models.CharField(max_length=500,blank=True)
    external_url=models.URLField(max_length=500,blank=True)
    sort_order=models.PositiveIntegerField()
    is_active=models.BooleanField(default=True)


class PublicSubmission(SpaceRecord):
    entry=models.ForeignKey(ContentEntry,on_delete=models.PROTECT)
    external_identity_id=models.UUIDField(db_index=True)
    author_display_name=models.CharField(max_length=150)
    body_text=models.TextField()
    status=models.CharField(max_length=10,default="pending")
    moderated_by=models.ForeignKey("auth.User",null=True,on_delete=models.PROTECT)
    moderated_at=models.DateTimeField(null=True)
    moderation_reason=models.CharField(max_length=500,blank=True)
    revision=models.PositiveIntegerField(default=1)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)

    class Meta:
        indexes=[models.Index(fields=["external_identity_id","-created_at"],name="cms_comment_window")]


class SitemapRefresh(models.Model):
    space=models.OneToOneField(PublicationSpace,primary_key=True,on_delete=models.CASCADE)
    epoch=models.PositiveBigIntegerField()
    due_at=models.DateTimeField(db_index=True)
