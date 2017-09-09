# -*- coding: utf-8 -*-
# Copyright: See the LICENSE file.


"""factory_boy extensions for use with the Django framework."""

from __future__ import absolute_import
from __future__ import unicode_literals

import os
import logging
import functools
import string

try:
    import django
    from django.core import files as django_files
    from django.db import models
    from django.db.models.fields import proxy
    from django.utils import timezone
except ImportError as e:  # pragma: no cover
    django = None
    django_files = None
    models = None
    timezone = None
    import_failure = e

from . import base
from . import declarations
from . import errors
from . import faker
from . import fuzzy
from .compat import BytesIO, is_string

logger = logging.getLogger('factory.generate')


DEFAULT_DB_ALIAS = 'default'  # Same as django.db.DEFAULT_DB_ALIAS


def require_django():
    """Simple helper to ensure Django is available."""
    if django_files is None:  # pragma: no cover
        raise import_failure


_LAZY_LOADS = {}


def get_model(app, model):
    """Wrapper around django's get_model."""
    if 'get_model' not in _LAZY_LOADS:
        _lazy_load_get_model()

    _get_model = _LAZY_LOADS['get_model']
    return _get_model(app, model)


def _lazy_load_get_model():
    """Lazy loading of get_model.

    get_model loads django.conf.settings, which may fail if
    the settings haven't been configured yet.
    """
    if django is None:
        def _get_model(app, model):
            raise import_failure
    else:
        from django import apps as django_apps
        _get_model = django_apps.apps.get_model
    _LAZY_LOADS['get_model'] = _get_model


def build_foreign_key(field_context):
    factory_class = DjangoModelFactory.auto_factory(
        field_context.field.related_model,
        exclude_auto_fields=field_context.skips,
    )
    return declarations.SubFactory(factory_class)


def build_manytomany(field_context):
    field = field_context.field

    try:
        remote_field = field.remote_field
    except AttributeError:
        remote_field = field.rel
    through = remote_field.through

    if through._meta.auto_created:
        # ManyToManyField without a through
        factory_class = DjangoModelFactory.auto_factory(field.related_model)

        def add_relateds(obj, create, _extracted, **_kwargs):
            manager = getattr(obj, field.name)
            for related in factory_class.create_batch(2):
                manager.add(related)
        return declarations.PostGeneration(add_relateds)
    else:
        # ManyToManyField with a through
        extra_fields = {field.m2m_field_name(): None}
        factory_class = DjangoModelFactory.auto_factory(
            through,
            **extra_fields)
        return declarations.RelatedFactory(factory_class, field.m2m_field_name())


def build_charfield(field_context):
    faker_rules = {
        'first_name': ['firstname', 'first_name'],
        'last_name': ['lastname', 'last_name'],
        'email': ['email'],
        'zipcode': ['zip', 'zipcode', 'postcode'],
    }
    for provider, names in faker_rules.items():
        if any(name in field_context.field_name for name in names):
            return faker.Faker(provider)
    return fuzzy.FuzzyText(length=field_context.field.max_length)


def build_choices(field_context):
    return fuzzy.FuzzyChoice(x[0] for x in field_context.field.choices)


def build_datetime(field_context):
    from django.conf import settings
    if settings.USE_TZ:
        return fuzzy.FuzzyDateTime(start_dt=timezone.now())
    else:
        return fuzzy.FuzzyNaiveDateTime(start_dt=timezone.now())


class DjangoIntrospector(base.BaseIntrospector):
    DEFAULT_BUILDERS = base.BaseIntrospector.DEFAULT_BUILDERS + [
        # Integers
        (models.IntegerField, lambda _ctxt: fuzzy.FuzzyInteger(low=-1000, high=1000)),
        (models.PositiveIntegerField, lambda _ctxt: fuzzy.FuzzyInteger(low=0, high=10000000)),
        (models.BigIntegerField, lambda _ctxt: fuzzy.FuzzyInteger(low=-9223372036854775808, high=9223372036854775807)),
        (models.PositiveSmallIntegerField, lambda _ctxt: fuzzy.FuzzyInteger(low=0, high=32767)),
        (models.SmallIntegerField, lambda _ctxt: fuzzy.FuzzyInteger(low=-32768, high=32767)),
        (models.DecimalField, lambda ctxt: fuzzy.FuzzyDecimal(
            low=-10 ** (ctxt.field.max_digits - ctxt.field.decimal_places) + 1,
            high=10 ** (ctxt.field.max_digits - ctxt.field.decimal_places) - 1,
            precision=ctxt.field.decimal_places,
        )),
        (models.FloatField, lambda _ctxt: fuzzy.FuzzyFloat(-1e50, 1e50)),

        # Text
        (models.CharField, build_charfield),
        (models.TextField, lambda _ctxt: fuzzy.FuzzyText(length=3000, chars=string.ascii_letters + ' .,?!\n')),
        (models.SlugField, lambda ctxt: fuzzy.FuzzyText(
            length=ctxt.field.max_length,
            chars=string.ascii_letters + string.digits + '-_',
        )),

        # Internet
        (models.EmailField, lambda _ctxt: faker.Faker('email')),
        (models.URLField, lambda _ctxt: faker.Faker('url')),
        (models.GenericIPAddressField, lambda ctxt: faker.Faker('ipv4' if ctxt.field.protocol == 'ipv4' else 'ipv6')),


        # Misc
        (models.BinaryField, lambda _ctxt: fuzzy.FuzzyBytes()),
        (models.BooleanField, lambda _ctxt: fuzzy.FuzzyChoice([True, False])),
        (models.NullBooleanField, lambda _ctxt: fuzzy.FuzzyChoice([None, True, False])),
        (models.FileField, lambda _ctxt: FileField()),
        (models.ImageField, lambda _ctxt: ImageField()),
        (models.UUIDField, lambda _ctxt: faker.Faker('uuid4')),

        # Date / Time
        (models.DateField, lambda _ctxt: fuzzy.FuzzyDate()),
        (models.DateTimeField, build_datetime),
        (models.TimeField, lambda _ctxt: fuzzy.FuzzyTime()),
        (models.DurationField, lambda _ctxt: faker.Faker('time_delta')),

        # Relational
        (models.ForeignKey, build_foreign_key),
        (models.OneToOneField, build_foreign_key),
        (models.ManyToManyField, build_manytomany),
    ]

    @classmethod
    def _is_default_field(cls, model, field):
        if field.auto_created:
            return False

        # Handle GenericForeignKey (untested!)
        if field.is_relation and field.related_model is None:
            assert field.concrete   # Are we sure about this?
            return False

        if not field.concrete:
            return False

        if isinstance(field, (models.AutoField, proxy.OrderWrt)):
            return False

        return cls._is_required_field(model, field)

    @classmethod
    def _is_required_field(cls, model, field):
        # Fields won't let you set an invalid default so if there is a default then it  must be ok
        if field.has_default():
            return False

        # Check if it will pass model field blank validation
        if not field.blank:
            return field.get_default() in field.empty_values

        # Check if it will pass DB NULL validation
        return not field.null and field.get_default() is None

    def get_default_field_names(self, model):
        return [field.name for field in model._meta.get_fields() if self._is_default_field(model, field)]

    def get_field_by_name(self, model, field_name):
        try:
            return model._meta.get_field(field_name)
        except django.core.exceptions.FieldDoesNotExist:
            return None

    def build_declaration(self, field_ctxt):
        if field_ctxt.field is None:
            return None
        if field_ctxt.field.choices:
            return build_choices(field_ctxt)
        return super(DjangoIntrospector, self).build_declaration(field_ctxt)


class DjangoOptions(base.FactoryOptions):
    DEFAULT_INTROSPECTOR_CLASS = DjangoIntrospector

    def _build_default_options(self):
        return super(DjangoOptions, self)._build_default_options() + [
            base.OptionDefault('django_get_or_create', (), inherit=True),
            base.OptionDefault('database', DEFAULT_DB_ALIAS, inherit=True),
        ]

    def _get_counter_reference(self):
        counter_reference = super(DjangoOptions, self)._get_counter_reference()
        if (counter_reference == self.base_factory
                and self.base_factory._meta.model is not None
                and self.base_factory._meta.model._meta.abstract
                and self.model is not None
                and not self.model._meta.abstract):
            # Target factory is for an abstract model, yet we're for another,
            # concrete subclass => don't reuse the counter.
            return self.factory
        return counter_reference

    def get_model_class(self):
        if is_string(self.model) and '.' in self.model:
            app, model_name = self.model.split('.', 1)
            self.model = get_model(app, model_name)

        return self.model


class DjangoModelFactory(base.Factory):
    """Factory for Django models.

    This makes sure that the 'sequence' field of created objects is a new id.

    Possible improvement: define a new 'attribute' type, AutoField, which would
    handle those for non-numerical primary keys.
    """

    _options_class = DjangoOptions

    class Meta:
        abstract = True  # Optional, but explicit.

    @classmethod
    def _load_model_class(cls, definition):

        if is_string(definition) and '.' in definition:
            app, model = definition.split('.', 1)
            return get_model(app, model)

        return definition

    @classmethod
    def _get_manager(cls, model_class):
        if model_class is None:
            raise errors.AssociatedClassError(
                "No model set on %s.%s.Meta" % (cls.__module__, cls.__name__))

        try:
            manager = model_class.objects
        except AttributeError:
            # When inheriting from an abstract model with a custom
            # manager, the class has no 'objects' field.
            manager = model_class._default_manager

        if cls._meta.database != DEFAULT_DB_ALIAS:
            manager = manager.using(cls._meta.database)
        return manager

    @classmethod
    def _get_or_create(cls, model_class, *args, **kwargs):
        """Create an instance of the model through objects.get_or_create."""
        manager = cls._get_manager(model_class)

        assert 'defaults' not in cls._meta.django_get_or_create, (
            "'defaults' is a reserved keyword for get_or_create "
            "(in %s._meta.django_get_or_create=%r)"
            % (cls, cls._meta.django_get_or_create))

        key_fields = {}
        for field in cls._meta.django_get_or_create:
            if field not in kwargs:
                raise errors.FactoryError(
                    "django_get_or_create - "
                    "Unable to find initialization value for '%s' in factory %s" %
                    (field, cls.__name__))
            key_fields[field] = kwargs.pop(field)
        key_fields['defaults'] = kwargs

        instance, _created = manager.get_or_create(*args, **key_fields)
        return instance

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Create an instance of the model, and save it to the database."""
        manager = cls._get_manager(model_class)

        if cls._meta.django_get_or_create:
            return cls._get_or_create(model_class, *args, **kwargs)

        return manager.create(*args, **kwargs)

    @classmethod
    def _after_postgeneration(cls, instance, create, results=None):
        """Save again the instance if creating and at least one hook ran."""
        if create and results:
            # Some post-generation hooks ran, and may have modified us.
            instance.save()


class FileField(declarations.Dict):
    """Helper to fill in django.db.models.FileField from a Factory."""

    DEFAULT_FILENAME = 'example.dat'

    def __init__(self, **defaults):
        require_django()
        super(FileField, self).__init__(defaults)

    def _make_data(self, params):
        """Create data for the field."""
        return params.get('data', b'')

    def _make_content(self, params):
        path = ''

        _content_params = [params.get('from_path'), params.get('from_file'), params.get('from_func')]
        if len([p for p in _content_params if p]) > 1:
            raise ValueError(
                "At most one argument from 'from_file', 'from_path', and 'from_func' should "
                "be non-empty when calling factory.django.FileField."
            )

        if params.get('from_path'):
            path = params['from_path']
            f = open(path, 'rb')
            content = django_files.File(f, name=path)

        elif params.get('from_file'):
            f = params['from_file']
            content = django_files.File(f)
            path = content.name

        elif params.get('from_func'):
            func = params['from_func']
            content = django_files.File(func())
            path = content.name

        else:
            data = self._make_data(params)
            content = django_files.base.ContentFile(data)

        if path:
            default_filename = os.path.basename(path)
        else:
            default_filename = self.DEFAULT_FILENAME

        filename = params.get('filename', default_filename)
        return filename, content

    def generate(self, step, params):
        """Fill in the field."""
        params = super(FileField, self).generate(step, params)
        filename, content = self._make_content(params)
        return django_files.File(content.file, filename)


class ImageField(FileField):
    DEFAULT_FILENAME = 'example.jpg'

    def _make_data(self, params):
        # ImageField (both django's and factory_boy's) require PIL.
        # Try to import it along one of its known installation paths.
        try:
            from PIL import Image
        except ImportError:
            import Image

        width = params.get('width', 100)
        height = params.get('height', width)
        color = params.get('color', 'blue')
        image_format = params.get('format', 'JPEG')

        thumb = Image.new('RGB', (width, height), color)
        thumb_io = BytesIO()
        thumb.save(thumb_io, format=image_format)
        return thumb_io.getvalue()


class mute_signals(object):
    """Temporarily disables and then restores any django signals.

    Args:
        *signals (django.dispatch.dispatcher.Signal): any django signals

    Examples:
        with mute_signals(pre_init):
            user = UserFactory.build()
            ...

        @mute_signals(pre_save, post_save)
        class UserFactory(factory.Factory):
            ...

        @mute_signals(post_save)
        def generate_users():
            UserFactory.create_batch(10)
    """

    def __init__(self, *signals):
        self.signals = signals
        self.paused = {}

    def __enter__(self):
        for signal in self.signals:
            logger.debug('mute_signals: Disabling signal handlers %r',
                         signal.receivers)

            # Note that we're using implementation details of
            # django.signals, since arguments to signal.connect()
            # are lost in signal.receivers
            self.paused[signal] = signal.receivers
            signal.receivers = []

    def __exit__(self, exc_type, exc_value, traceback):
        for signal, receivers in self.paused.items():
            logger.debug('mute_signals: Restoring signal handlers %r',
                         receivers)

            signal.receivers = receivers
            with signal.lock:
                # Django uses some caching for its signals.
                # Since we're bypassing signal.connect and signal.disconnect,
                # we have to keep messing with django's internals.
                signal.sender_receivers_cache.clear()
        self.paused = {}

    def copy(self):
        return mute_signals(*self.signals)

    def __call__(self, callable_obj):
        if isinstance(callable_obj, base.FactoryMetaClass):
            # Retrieve __func__, the *actual* callable object.
            generate_method = callable_obj._generate.__func__

            @classmethod
            @functools.wraps(generate_method)
            def wrapped_generate(*args, **kwargs):
                # A mute_signals() object is not reentrant; use a copy everytime.
                with self.copy():
                    return generate_method(*args, **kwargs)

            callable_obj._generate = wrapped_generate
            return callable_obj

        else:
            @functools.wraps(callable_obj)
            def wrapper(*args, **kwargs):
                # A mute_signals() object is not reentrant; use a copy everytime.
                with self.copy():
                    return callable_obj(*args, **kwargs)
            return wrapper
