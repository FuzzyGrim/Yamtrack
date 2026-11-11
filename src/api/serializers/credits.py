from rest_framework import serializers

from .common import PaginationSerializer


class CastMemberSerializer(serializers.Serializer):
    """Serializer for a single cast member."""

    person_id = serializers.IntegerField()
    name = serializers.CharField()
    character = serializers.CharField(allow_null=True)
    image = serializers.URLField(allow_null=True)
    order = serializers.IntegerField(allow_null=True)
    episode_count = serializers.IntegerField(allow_null=True)


class PaginatedCastSerializer(serializers.Serializer):
    """Paginated response serializer for cast members."""

    pagination = PaginationSerializer()
    results = CastMemberSerializer(many=True)


class CrewMemberSerializer(serializers.Serializer):
    """Serializer for a single crew member."""

    person_id = serializers.IntegerField()
    name = serializers.CharField()
    department = serializers.CharField(allow_null=True)
    jobs = serializers.ListField(child=serializers.CharField())
    image = serializers.URLField(allow_null=True)
    episode_count = serializers.IntegerField(allow_null=True)


class PaginatedCrewSerializer(serializers.Serializer):
    """Paginated response serializer for crew members."""

    pagination = PaginationSerializer()
    results = CrewMemberSerializer(many=True)


class CreditSerializer(serializers.Serializer):
    """Serializer for a creator or character of a media."""

    id = serializers.CharField(allow_null=True)
    name = serializers.CharField()
    roles = serializers.ListField(child=serializers.CharField())
    image = serializers.URLField(allow_null=True)
    issue_count = serializers.IntegerField(allow_null=True)
    source_url = serializers.URLField(allow_null=True)


class PaginatedCreditsSerializer(serializers.Serializer):
    """Paginated response serializer for creators or characters."""

    pagination = PaginationSerializer()
    results = CreditSerializer(many=True)


class CompanySerializer(serializers.Serializer):
    """Serializer for a company involved in a media."""

    company_id = serializers.IntegerField()
    name = serializers.CharField()
    logo = serializers.URLField(allow_null=True)
    roles = serializers.ListField(child=serializers.CharField())


class PaginatedCompaniesSerializer(serializers.Serializer):
    """Paginated response serializer for companies."""

    pagination = PaginationSerializer()
    results = CompanySerializer(many=True)
