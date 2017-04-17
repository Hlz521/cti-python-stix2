import re
import uuid
from six import PY2
import datetime as dt
import pytz
from dateutil import parser
import inspect
from .base import _STIXBase


class Property(object):
    """Represent a property of STIX data type.

    Subclasses can define the following attributes as keyword arguments to
    __init__():

    - `required` - If `True`, the property must be provided when creating an
        object with that property. No default value exists for these properties.
        (Default: `False`)
    - `fixed` - This provides a constant default value. Users are free to
        provide this value explicity when constructing an object (which allows
        you to copy *all* values from an existing object to a new object), but
        if the user provides a value other than the `fixed` value, it will raise
        an error. This is semantically equivalent to defining both:
        - a `validate()` function that checks if the value matches the fixed
          value, and
        - a `default()` function that returns the fixed value.
        (Default: `None`)

    Subclasses can also define the following functions.

    - `def clean(self, value) -> any:`
        - Transform `value` into a valid value for this property. This should
          raise a ValueError if such no such transformation is possible.
    - `def validate(self, value) -> any:`
        - check that `value` is valid for this property. This should return
          a valid value (possibly modified) for this property, or raise a
          ValueError if the value is not valid.
          (Default: if `clean` is defined, it will attempt to call `clean` and
          return the result or pass on a ValueError that `clean` raises. If
          `clean` is not defined, this will return `value` unmodified).
    - `def default(self):`
        - provide a default value for this property.
        - `default()` can return the special value `NOW` to use the current
            time. This is useful when several timestamps in the same object need
            to use the same default value, so calling now() for each field--
            likely several microseconds apart-- does not work.

    Subclasses can instead provide lambda functions for `clean`, and `default`
    as keyword arguments. `validate` should not be provided as a lambda since
    lambdas cannot raise their own exceptions.
    """

    def _default_validate(self, value):
        if value != self._fixed_value:
            raise ValueError("must equal '{0}'.".format(self._fixed_value))
        return value

    def __init__(self, required=False, fixed=None, clean=None, default=None, type=None):
        self.required = required
        self.type = type
        if fixed:
            self._fixed_value = fixed
            self.validate = self._default_validate
            self.default = lambda: fixed
        if clean:
            self.clean = clean
        if default:
            self.default = default

    def clean(self, value):
        raise NotImplementedError

    def validate(self, value):
        try:
            value = self.clean(value)
        except NotImplementedError:
            pass
        return value

    def __call__(self, value=None):
        if value is not None:
            return value


class ListProperty(Property):

    def __init__(self, contained, **kwargs):
        """
        Contained should be a function which returns an object from the value.
        """
        if contained == StringProperty:
            self.contained = StringProperty().string_type
        elif contained == BooleanProperty:
            self.contained = bool
        elif inspect.isclass(contained) and issubclass(contained, Property):
            # If it's a class and not an instance, instantiate it so that
            # validate() can be called on it, and ListProperty.validate() will
            # use __call__ when it appends the item.
            self.contained = contained()
        else:
            self.contained = contained
        super(ListProperty, self).__init__(**kwargs)

    def validate(self, value):
        try:
            iter(value)
        except TypeError:
            raise ValueError("must be an iterable.")

        result = []
        for item in value:
            try:
                valid = self.contained.validate(item)
            except ValueError:
                raise
            except AttributeError:
                # type of list has no validate() function (eg. built in Python types)
                # TODO Should we raise an error here?
                valid = item

            if type(valid) is dict:
                result.append(self.contained(**valid))
            else:
                result.append(self.contained(valid))

        # STIX spec forbids empty lists
        if len(result) < 1:
            raise ValueError("must not be empty.")

        return result


class StringProperty(Property):

    def __init__(self, **kwargs):
        if PY2:
            self.string_type = unicode
        else:
            self.string_type = str
        super(StringProperty, self).__init__(**kwargs)

    def clean(self, value):
        return self.string_type(value)

    def validate(self, value):
        try:
            val = self.clean(value)
        except ValueError:
            raise
        return val


class TypeProperty(Property):
    def __init__(self, type):
        super(TypeProperty, self).__init__(fixed=type)


class IDProperty(Property):

    def __init__(self, type):
        self.required_prefix = type + "--"
        super(IDProperty, self).__init__()

    def validate(self, value):
        if not value.startswith(self.required_prefix):
            raise ValueError("must start with '{0}'.".format(self.required_prefix))
        try:
            uuid.UUID(value.split('--', 1)[1], version=4)
        except Exception:
            raise ValueError("must have a valid version 4 UUID after the prefix.")
        return value

    def default(self):
        return self.required_prefix + str(uuid.uuid4())


class BooleanProperty(Property):

    def clean(self, value):
        if isinstance(value, bool):
            return value

        trues = ['true', 't']
        falses = ['false', 'f']
        try:
            if value.lower() in trues:
                return True
            if value.lower() in falses:
                return False
        except AttributeError:
            if value == 1:
                return True
            if value == 0:
                return False

        raise ValueError("not a coercible boolean value.")

    def validate(self, value):
        try:
            return self.clean(value)
        except ValueError:
            raise ValueError("must be a boolean value.")


class TimestampProperty(Property):

    def validate(self, value):
        if isinstance(value, dt.date):
            if hasattr(value, 'hour'):
                return value
            else:
                # Add a time component
                return dt.datetime.combine(value, dt.time(), tzinfo=pytz.timezone('US/Eastern'))

        # value isn't a date or datetime object so assume it's a string
        try:
            parsed = parser.parse(value)
        except TypeError:
            # Unknown format
            raise ValueError("must be a datetime object, date object, or "
                             "timestamp string in a recognizable format.")
        if parsed.tzinfo:
            return parsed.astimezone(pytz.utc)
        else:
            # Doesn't have timezone info in the string; assume UTC
            # TODO Should we default to system local timezone instead?
            return pytz.utc.localize(parsed)


REF_REGEX = re.compile("^[a-z][a-z-]+[a-z]--[0-9a-fA-F]{8}-[0-9a-fA-F]{4}"
                       "-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class ReferenceProperty(Property):
    def __init__(self, required=False, type=None):
        """
        references sometimes must be to a specific object type
        """
        self.type = type
        super(ReferenceProperty, self).__init__(required, type=type)

    def validate(self, value):
        if isinstance(value, _STIXBase):
            value = value.id
        if self.type:
            if not value.startswith(self.type):
                raise ValueError("must start with '{0}'.".format(self.type))
        if not REF_REGEX.match(value):
            raise ValueError("must match <object-type>--<guid>.")
        return value


SELECTOR_REGEX = re.compile("^[a-z0-9_-]{3,250}(\\.(\\[\\d+\\]|[a-z0-9_-]{1,250}))*$")


class SelectorProperty(Property):
    def __init__(self, type=None):
        # ignore type
        super(SelectorProperty, self).__init__()

    def validate(self, value):
        if not SELECTOR_REGEX.match(value):
            raise ValueError("values must adhere to selector syntax")
        return value
