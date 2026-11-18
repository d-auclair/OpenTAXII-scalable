"""Taxii2 validation functions."""

import datetime
import json
from typing import List, Mapping, Union

import structlog
from marshmallow import Schema, fields, validate
from stix2 import parse
from stix2.exceptions import STIXError

from opentaxii.persistence.api import OpenTAXII2PersistenceAPI
from opentaxii.taxii2.exceptions import ValidationError
from opentaxii.taxii2.utils import DATETIMEFORMAT

log = structlog.get_logger(__name__)


def validate_envelope(
    json_data: Union[str, bytes, Mapping], allow_custom: bool = False
) -> None:
    """
    Validate if ``json_data`` is a valid taxii2 envelope.

    :param json_data: the data to check
    :param allow_custom: if true, allow non-standard stix types
    """
    if not json_data:
        raise ValidationError("No data")
    if isinstance(json_data, Mapping):
        data = json_data
    else:
        try:
            data = json.loads(json_data)
        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid json: {str(e)}") from e
    if "objects" not in data:
        raise ValidationError("No objects")
    for index, item in enumerate(data["objects"]):
        try:
            parse(item, allow_custom)
        except STIXError as e:
            log.error(
                "taxii2.envelope.object.invalid",
                index=index,
                stix_id=item.get("id"),
                stix_type=item.get("type"),
                error=str(e),
            )
            raise ValidationError(
                f"Invalid stix object: {json.dumps(item)}; {str(e)}"
            ) from e


def validate_delete_envelope(data: Mapping) -> List[str]:
    """Validate the OpenTAXII bulk-delete extension payload."""
    if not isinstance(data, Mapping):
        raise ValidationError("Bulk delete payload must be a JSON object")
    object_ids = data.get("object_ids")
    if not isinstance(object_ids, list) or not object_ids:
        raise ValidationError("object_ids must be a non-empty list")
    if any(not isinstance(object_id, str) or not object_id for object_id in object_ids):
        raise ValidationError("Each object_ids entry must be a non-empty string")
    return list(dict.fromkeys(object_ids))


class Taxii2DateTime(fields.DateTime):
    """Taxii2 formatting compliant datetime field."""

    DEFAULT_FORMAT = DATETIMEFORMAT

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        return value.replace(tzinfo=datetime.timezone.utc)


class Taxii2Next(fields.Field):
    """Implemenatation of the taxii2 `next` query param."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        try:
            value = (
                self.parent.persistence_api.parse_next_param(  # type:ignore[union-attr]
                    value
                )
            )
        except:  # noqa
            raise ValidationError("Not a valid value.")
        return value


class Taxii2Filter(fields.Field):
    """General taxii2 filter implementation."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        return value.split(",")


class Taxii2VersionFilter(Taxii2Filter):
    """Taxii2 compliant version filter."""

    def _deserialize(self, value, attr, data, **kwargs):
        values = super()._deserialize(value, attr, data, **kwargs)
        new_values = []
        for value in values:
            if value not in ["first", "last", "all"]:
                try:
                    value = datetime.datetime.strptime(value, DATETIMEFORMAT).replace(
                        tzinfo=datetime.timezone.utc
                    )
                except ValueError:
                    pass
            new_values.append(value)
        return new_values


class PersistenceApiMxin:
    """Store persistence api on schema instance, to reference in `Taxii2Next`"""

    def __init__(self, persistence_api: OpenTAXII2PersistenceAPI, *args, **kwargs):
        self.persistence_api = persistence_api
        super().__init__(*args, **kwargs)


class VersionFilterParamsSchema(PersistenceApiMxin, Schema):
    """Schema for the versions endpoint filters."""

    limit = fields.Int(validate=validate.Range(min=1))
    added_after = Taxii2DateTime()
    next_kwargs = Taxii2Next(data_key="next")
    match_spec_version = Taxii2Filter(data_key="match[spec_version]")


class ObjectFilterParamsSchema(PersistenceApiMxin, Schema):
    """Schema for the object endpoint filters."""

    limit = fields.Int(validate=validate.Range(min=1))
    added_after = Taxii2DateTime()
    next_kwargs = Taxii2Next(data_key="next")
    match_spec_version = Taxii2Filter(data_key="match[spec_version]")
    match_version = Taxii2VersionFilter(data_key="match[version]")


class ListFilterParamsSchema(PersistenceApiMxin, Schema):
    """Schema for the object list endpoint filters."""

    limit = fields.Int(validate=validate.Range(min=1))
    added_after = Taxii2DateTime()
    next_kwargs = Taxii2Next(data_key="next")
    match_spec_version = Taxii2Filter(data_key="match[spec_version]")
    match_version = Taxii2VersionFilter(data_key="match[version]")
    match_id = Taxii2Filter(data_key="match[id]")
    match_type = Taxii2Filter(data_key="match[type]")


class DeleteFilterParamsSchema(Schema):
    """Schema for the object delete endpoint filters."""

    match_version = Taxii2VersionFilter(data_key="match[version]")
    match_spec_version = Taxii2Filter(data_key="match[spec_version]")


def validate_object_filter_params(
    filter_params: Mapping, persistence_api: OpenTAXII2PersistenceAPI
) -> dict:
    """Validate and load filter params for the object endpoint."""
    parsed_params = ObjectFilterParamsSchema(persistence_api).load(filter_params)
    return parsed_params


def validate_list_filter_params(
    filter_params: Mapping, persistence_api: OpenTAXII2PersistenceAPI
) -> dict:
    """Validate and load filter params for the list endpoint."""
    parsed_params = ListFilterParamsSchema(persistence_api).load(filter_params)
    return parsed_params


def validate_versions_filter_params(
    filter_params: Mapping, persistence_api: OpenTAXII2PersistenceAPI
) -> dict:
    """Validate and load filter params for the versions endpoint."""
    parsed_params = VersionFilterParamsSchema(persistence_api).load(filter_params)
    return parsed_params


def validate_delete_filter_params(filter_params: Mapping) -> dict:
    """Validate and load filter params for the delete endpoint."""
    parsed_params = DeleteFilterParamsSchema().load(filter_params)
    return parsed_params
