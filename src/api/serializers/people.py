from rest_framework import serializers

from .common import PaginationSerializer
from .fields import MediaSourceChoiceField, MediaTypeChoiceField
from .media import RelatedResponseSerializer


class SearchPersonSerializer(serializers.Serializer):
    """Serializer for individual people in search results."""

    person_id = serializers.IntegerField()
    source = MediaSourceChoiceField()
    name = serializers.CharField()
    image = serializers.URLField(allow_null=True)
    known_for_department = serializers.CharField(allow_null=True)
    known_for = RelatedResponseSerializer(many=True)


class SearchPeopleResponseSerializer(serializers.Serializer):
    """Serializer for people search endpoint results."""

    pagination = PaginationSerializer()
    results = SearchPersonSerializer(many=True)


class PersonSerializer(serializers.Serializer):
    """Serializer for person details."""

    person_id = serializers.IntegerField()
    source = MediaSourceChoiceField()
    source_url = serializers.URLField()
    name = serializers.CharField()
    image = serializers.URLField(allow_null=True)
    biography = serializers.CharField(allow_null=True)
    birthday = serializers.DateField(allow_null=True)
    deathday = serializers.DateField(allow_null=True)
    place_of_birth = serializers.CharField(allow_null=True)
    known_for_department = serializers.CharField(allow_null=True)
    also_known_as = serializers.ListField(child=serializers.CharField())
    homepage = serializers.URLField(allow_null=True)
    external_links = serializers.DictField(child=serializers.URLField())


class KnownForSerializer(serializers.Serializer):
    """Serializer for a media a person is known for."""

    source = MediaSourceChoiceField()
    media_type = MediaTypeChoiceField()
    media_id = serializers.CharField()
    title = serializers.CharField()
    image = serializers.URLField(allow_null=True)
    character = serializers.CharField(allow_null=True)
    departments = serializers.ListField(child=serializers.CharField())
    jobs = serializers.ListField(child=serializers.CharField())
    release_date = serializers.DateField(allow_null=True)
    episode_count = serializers.IntegerField(allow_null=True)


class PaginatedKnownForSerializer(serializers.Serializer):
    """Paginated response serializer for known for media."""

    pagination = PaginationSerializer()
    results = KnownForSerializer(many=True)
