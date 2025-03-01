# -*- coding: utf-8 -*-
# Copyright: See the LICENSE file.

from __future__ import unicode_literals

import collections
import logging
import warnings

from . import builder
from . import declarations
from . import enums
from . import errors
from . import utils

logger = logging.getLogger('factory.generate')

# Factory metaclasses


def get_factory_bases(bases):
    """Retrieve all FactoryMetaClass-derived bases from a list."""
    return [b for b in bases if issubclass(b, BaseFactory)]


def resolve_attribute(name, bases, default=None):
    """Find the first definition of an attribute according to MRO order."""
    for base in bases:
        if hasattr(base, name):
            return getattr(base, name)
    return default


class FactoryMetaClass(type):
    """Factory metaclass for handling ordered declarations."""

    def __call__(cls, **kwargs):
        """Override the default Factory() syntax to call the default strategy.

        Returns an instance of the associated class.
        """

        if cls._meta.strategy == enums.BUILD_STRATEGY:
            return cls.build(**kwargs)
        elif cls._meta.strategy == enums.CREATE_STRATEGY:
            return cls.create(**kwargs)
        elif cls._meta.strategy == enums.STUB_STRATEGY:
            return cls.stub(**kwargs)
        else:
            raise errors.UnknownStrategy('Unknown Meta.strategy: {0}'.format(
                cls._meta.strategy))

    def __new__(mcs, class_name, bases, attrs):
        """Record attributes as a pattern for later instance construction.

        This is called when a new Factory subclass is defined; it will collect
        attribute declaration from the class definition.

        Args:
            class_name (str): the name of the class being created
            bases (list of class): the parents of the class being created
            attrs (str => obj dict): the attributes as defined in the class
                definition

        Returns:
            A new class
        """
        parent_factories = get_factory_bases(bases)
        if parent_factories:
            base_factory = parent_factories[0]
        else:
            base_factory = None

        attrs_meta = attrs.pop('Meta', None)
        attrs_params = attrs.pop('Params', None)

        base_meta = resolve_attribute('_meta', bases)
        options_class = resolve_attribute('_options_class', bases, FactoryOptions)

        meta = options_class()
        attrs['_meta'] = meta

        new_class = super(FactoryMetaClass, mcs).__new__(
            mcs, class_name, bases, attrs)

        meta.contribute_to_class(
            new_class,
            meta=attrs_meta,
            base_meta=base_meta,
            base_factory=base_factory,
            params=attrs_params,
        )

        return new_class

    def __str__(cls):
        if cls._meta.abstract:
            return '<%s (abstract)>' % cls.__name__
        else:
            return '<%s for %s>' % (cls.__name__, cls._meta.model)


class BaseMeta:
    abstract = True
    strategy = enums.CREATE_STRATEGY


class OptionDefault(object):
    """The default for an option.

    Attributes:
        name: str, the name of the option ('class Meta' attribute)
        value: object, the default value for the option
        inherit: bool, whether to inherit the value from the parent factory's `class Meta`
            when no value is provided
        checker: callable or None, an optional function used to detect invalid option
            values at declaration time
    """
    def __init__(self, name, value, inherit=False, checker=None):
        self.name = name
        self.value = value
        self.inherit = inherit
        self.checker = checker

    def apply(self, meta, base_meta):
        value = self.value
        if self.inherit and base_meta is not None:
            value = getattr(base_meta, self.name, value)
        if meta is not None:
            value = getattr(meta, self.name, value)

        if self.checker is not None:
            self.checker(meta, value)

        return value

    def __str__(self):
        return '%s(%r, %r, inherit=%r)' % (
            self.__class__.__name__,
            self.name, self.value, self.inherit)


FieldContext = collections.namedtuple(
    'FieldContext',
    ['field', 'field_name', 'model', 'factory', 'skips'])


class BaseIntrospector(object):
    """Introspector for models.

    Extracts declarations from a model.

    Attributes:
        DEFAULT_BUILDERS ((field_class, callable) list): maps a field_class
            to a callable able to build a declaration from it
    """

    DEFAULT_BUILDERS = []

    def __init__(self, factory_class):
        # Don't use a dict as DEFAULT_BUILDERS to avoid issues
        # when inheriting an Introspector.
        self.builders = dict(self.DEFAULT_BUILDERS)
        self._factory_class = factory_class
        self._model = self._factory_class._meta.model

    def get_default_field_names(self, model):
        """
        Fetch default "auto-declarable" field names from a model.
        Override to define what fields are included by default
        """
        raise NotImplementedError("Introspector %r doesn't know how to extract fields from %s" % (self, model))

    def get_field_by_name(self, model, field_name):
        """
        Get the actual "field descriptor" for a given field name
        Actual return value will depend on your underlying lib
        May return None if the field does not exist
        """
        raise NotImplementedError(
            "Introspector %r doesn't know how to fetch field %s from %r"
            % (self._factory_class, field_name, model))

    def build_declaration(self, field_ctxt):
        """Build a factory.Declaration from a FieldContext

        Note that FieldContext may be None if get_field_by_name() returned None

        Relies on ``self.DEFAULT_BUILDERS``.

        Override to customise field generation.

        Returns:
            factory.Declaration or None.
        """
        if field_ctxt.field is None:
            return None

        field = field_ctxt.field
        if field.__class__ not in self.builders:
            raise NotImplementedError(
                "Introspector %r lacks recipe for building field %r; add it to %s.Meta.auto_fields_rules."
                % (self, field, self._factory_class.__name__))
        builder = self.builders[field.__class__]
        return builder(field_ctxt)

    def build_declarations(self, for_fields, skip_fields):
        """Build declarations for a set of fields.

        Args:
            for_fields (str iterable): list of fields to build
            skip_fields (str iterable): list of fields that should *NOT* be built.

        Returns:
            (str, factory.Declaration) list: the new declarations.
        """
        declarations = {}
        for field_name in for_fields:
            if field_name in skip_fields:
                continue

            sub_skip_pattern = '%s__' % field_name
            sub_skips = [
                sk[len(sub_skip_pattern):]
                for sk in skip_fields
                if sk.startswith(sub_skip_pattern)
            ]

            field = self.get_field_by_name(self._model, field_name)

            field_ctxt = FieldContext(
                field=field,
                field_name=field_name,
                model=self._model,
                factory=self._factory_class,
                skips=sub_skips,
            )
            declaration = self.build_declaration(field_ctxt)
            if declaration is not None:
                declarations[field_name] = declaration

        return declarations

    def __repr__(self):
        return '<%s for %s>' % (self.__class__.__name__, self._factory_class.__name__)


class FactoryOptions(object):
    DEFAULT_INTROSPECTOR_CLASS = BaseIntrospector

    def __init__(self):
        self.factory = None
        self.base_factory = None
        self.base_declarations = {}
        self.parameters = {}
        self.parameters_dependencies = {}
        self.pre_declarations = builder.DeclarationSet()
        self.post_declarations = builder.DeclarationSet()
        self.introspector = None

        self._counter = None
        self.counter_reference = None

    @property
    def declarations(self):
        base_declarations = dict(self.base_declarations)
        for name, param in utils.sort_ordered_objects(self.parameters.items(), getter=lambda item: item[1]):
            base_declarations.update(param.as_declarations(name, base_declarations))
        return base_declarations

    def _build_default_options(self):
        """"Provide the default value for all allowed fields.

        Custom FactoryOptions classes should override this method
        to update() its return value.
        """
        return [
            # The model this factory build
            OptionDefault('model', None, inherit=True),
            # Whether this factory is allowed to build objects
            OptionDefault('abstract', False, inherit=False),
            # The default strategy (BUILD_STRATEGY or CREATE_STRATEGY)
            OptionDefault('strategy', enums.CREATE_STRATEGY, inherit=True),
            # Declarations that should be passed as *args instead
            OptionDefault('inline_args', (), inherit=True),
            # Declarations that shouldn't be passed to the object
            OptionDefault('exclude', (), inherit=True),
            # Declarations that should be used under another name
            # to the target model (for name conflict handling)
            OptionDefault('rename', {}, inherit=True),
            # The introspector class to use; if None (the default),
            # uses self.DEFAULT_INTROSPECTOR_CLASS
            OptionDefault('introspector_class', None, inherit=True),
            # Whether to auto-generate the default set of fields
            OptionDefault('default_auto_fields', False, inherit=True),

            # include_auto_fields and exclude_auto_fields inheritance is handled
            # as a combined special case in contribute_to_class()

            # List of fields to include in auto-generation
            OptionDefault('include_auto_fields', (), inherit=False),
            # List of fields to exclude from auto-generation
            OptionDefault('exclude_auto_fields', (), inherit=False),
        ]

    def _fill_from_meta(self, meta, base_meta):
        # Exclude private/protected fields from the meta
        if meta is None:
            meta_attrs = {}
        else:
            meta_attrs = dict(
                (k, v)
                for (k, v) in vars(meta).items()
                if not k.startswith('_')
            )

        for option in self._build_default_options():
            assert not hasattr(self, option.name), "Can't override field %s." % option.name
            value = option.apply(meta, base_meta)
            meta_attrs.pop(option.name, None)
            setattr(self, option.name, value)

        if meta_attrs:
            # Some attributes in the Meta aren't allowed here
            raise TypeError(
                "'class Meta' for %r got unknown attribute(s) %s"
                % (self.factory, ','.join(sorted(meta_attrs.keys()))))

    def contribute_to_class(self, factory, meta=None, base_meta=None, base_factory=None, params=None):

        self.factory = factory
        self.base_factory = base_factory

        self._fill_from_meta(meta=meta, base_meta=base_meta)

        if self.introspector_class is None:
            # Due to OptionDefault inheritance handling,
            # we don't want to set self.introspector_class
            # as that would break the default.
            self.introspector = self.DEFAULT_INTROSPECTOR_CLASS(factory)
        else:
            self.introspector = self.introspector_class(factory)

        self.model = self.get_model_class()
        if self.model is None:
            self.abstract = True

        self.counter_reference = self._get_counter_reference()

        # Scan the inheritance chain, starting from the furthest point,
        # excluding the current class, to retrieve all declarations.
        for parent in reversed(self.factory.__mro__[1:]):
            if not hasattr(parent, '_meta'):
                continue
            self.base_declarations.update(parent._meta.base_declarations)
            self.parameters.update(parent._meta.parameters)

        for k, v in vars(self.factory).items():
            if self._is_declaration(k, v):
                self.base_declarations[k] = v

        if not self.abstract and (self.default_auto_fields or self.include_auto_fields):
            field_names = set()
            if self.default_auto_fields:
                field_names.update(self.introspector.get_default_field_names(self.model))

            # Apply include_auto_fields/exclude_auto_fields from inheritance chain
            factory_parents = [f for f in reversed(self.factory.__mro__[1:]) if hasattr(f, '_meta')]
            for parent in factory_parents:
                field_names.update(getattr(parent._meta, 'include_auto_fields', []))
                field_names.difference_update(getattr(parent._meta, 'exclude_auto_fields', []))

            field_names.update(self.include_auto_fields)

            exclude_auto_fields = set(self.base_declarations.keys())
            exclude_auto_fields.update(self.exclude_auto_fields)
            field_names.difference_update(exclude_auto_fields)

            auto_declarations = self.introspector.build_declarations(field_names, exclude_auto_fields)

            for field_name, auto_declaration in auto_declarations.items():
                if field_name not in field_names:
                    raise ValueError(
                        'Introspector %s returned a field (%s) that it was not asked for'
                        % (self.introspector.__class__.__name__, field_name))
                self.base_declarations[field_name] = auto_declaration

        if params is not None:
            for k, v in utils.sort_ordered_objects(vars(params).items(), getter=lambda item: item[1]):
                if not k.startswith('_'):
                    self.parameters[k] = declarations.SimpleParameter.wrap(v)

        self._check_parameter_dependencies(self.parameters)

        self.pre_declarations, self.post_declarations = builder.parse_declarations(self.declarations)

    def _get_counter_reference(self):
        """Identify which factory should be used for a shared counter."""

        if (self.model is not None
                and self.base_factory is not None
                and self.base_factory._meta.model is not None
                and issubclass(self.model, self.base_factory._meta.model)):
            return self.base_factory._meta.counter_reference
        else:
            return self

    def _initialize_counter(self):
        """Initialize our counter pointer.

        If we're the top-level factory, instantiate a new counter
        Otherwise, point to the top-level factory's counter.
        """
        if self._counter is not None:
            return

        if self.counter_reference is self:
            self._counter = _Counter(seq=self.factory._setup_next_sequence())
        else:
            self.counter_reference._initialize_counter()
            self._counter = self.counter_reference._counter

    def next_sequence(self):
        """Retrieve a new sequence ID.

        This will call, in order:
        - next_sequence from the base factory, if provided
        - _setup_next_sequence, if this is the 'toplevel' factory and the
            sequence counter wasn't initialized yet; then increase it.
        """
        self._initialize_counter()
        return self._counter.next()

    def reset_sequence(self, value=None, force=False):
        self._initialize_counter()

        if self.counter_reference is not self and not force:
            raise ValueError(
                "Can't reset a sequence on descendant factory %r; reset sequence on %r or use `force=True`."
                % (self.factory, self.counter_reference.factory))

        if value is None:
            value = self.counter_reference.factory._setup_next_sequence()
        self._counter.reset(value)

    def prepare_arguments(self, attributes):
        """Convert an attributes dict to a (args, kwargs) tuple."""
        kwargs = dict(attributes)
        # 1. Extension points
        kwargs = self.factory._adjust_kwargs(**kwargs)

        # 2. Remove hidden objects
        kwargs = {
            k: v for k, v in kwargs.items()
            if k not in self.exclude and k not in self.parameters and v is not declarations.SKIP
        }

        # 3. Rename fields
        for old_name, new_name in self.rename.items():
            kwargs[new_name] = kwargs.pop(old_name)

        # 4. Extract inline args
        args = tuple(
            kwargs.pop(arg_name)
            for arg_name in self.inline_args
        )

        return args, kwargs

    def instantiate(self, step, args, kwargs):
        model = self.get_model_class()

        if step.builder.strategy == enums.BUILD_STRATEGY:
            return self.factory._build(model, *args, **kwargs)
        elif step.builder.strategy == enums.CREATE_STRATEGY:
            return self.factory._create(model, *args, **kwargs)
        else:
            assert step.builder.strategy == enums.STUB_STRATEGY
            return StubObject(**kwargs)

    def use_postgeneration_results(self, step, instance, results):
        self.factory._after_postgeneration(
            instance,
            create=step.builder.strategy == enums.CREATE_STRATEGY,
            results=results,
        )

    def _is_declaration(self, name, value):
        """Determines if a class attribute is a field value declaration.

        Based on the name and value of the class attribute, return ``True`` if
        it looks like a declaration of a default field value, ``False`` if it
        is private (name starts with '_') or a classmethod or staticmethod.

        """
        if isinstance(value, (classmethod, staticmethod)):
            return False
        elif enums.get_builder_phase(value):
            # All objects with a defined 'builder phase' are declarations.
            return True
        return not name.startswith("_")

    def _check_parameter_dependencies(self, parameters):
        """Find out in what order parameters should be called."""
        # Warning: parameters only provide reverse dependencies; we reverse them into standard dependencies.
        # deep_revdeps: set of fields a field depend indirectly upon
        deep_revdeps = collections.defaultdict(set)
        # Actual, direct dependencies
        deps = collections.defaultdict(set)

        for name, parameter in parameters.items():
            if isinstance(parameter, declarations.Parameter):
                field_revdeps = parameter.get_revdeps(parameters)
                if not field_revdeps:
                    continue
                deep_revdeps[name] = set.union(*(deep_revdeps[dep] for dep in field_revdeps))
                deep_revdeps[name] |= set(field_revdeps)
                for dep in field_revdeps:
                    deps[dep].add(name)

        # Check for cyclical dependencies
        cyclic = [name for name, field_deps in deep_revdeps.items() if name in field_deps]
        if cyclic:
            raise errors.CyclicDefinitionError(
                "Cyclic definition detected on %r; Params around %s"
                % (self.factory, ', '.join(cyclic)))
        return deps

    def get_model_class(self):
        """Extension point for loading model classes.

        This can be overridden in framework-specific subclasses to hook into
        existing model repositories, for instance.
        """
        return self.model

    def __str__(self):
        return "<%s for %s>" % (self.__class__.__name__, self.factory.__class__.__name__)

    def __repr__(self):
        return str(self)


# Factory base classes


class _Counter(object):
    """Simple, naive counter.

    Attributes:
        for_class (obj): the class this counter related to
        seq (int): the next value
    """

    def __init__(self, seq):
        self.seq = seq

    def next(self):
        value = self.seq
        self.seq += 1
        return value

    def reset(self, next_value=0):
        self.seq = next_value


class BaseFactory(object):
    """Factory base support for sequences, attributes and stubs."""

    # Backwards compatibility
    UnknownStrategy = errors.UnknownStrategy
    UnsupportedStrategy = errors.UnsupportedStrategy

    def __new__(cls, *args, **kwargs):
        """Would be called if trying to instantiate the class."""
        raise errors.FactoryError('You cannot instantiate BaseFactory')

    _meta = FactoryOptions()

    # ID to use for the next 'declarations.Sequence' attribute.
    _counter = None

    @classmethod
    def reset_sequence(cls, value=None, force=False):
        """Reset the sequence counter.

        Args:
            value (int or None): the new 'next' sequence value; if None,
                recompute the next value from _setup_next_sequence().
            force (bool): whether to force-reset parent sequence counters
                in a factory inheritance chain.
        """
        cls._meta.reset_sequence(value, force=force)

    @classmethod
    def _setup_next_sequence(cls):
        """Set up an initial sequence value for Sequence attributes.

        Returns:
            int: the first available ID to use for instances of this factory.
        """
        return 0

    @classmethod
    def attributes(cls, create=False, extra=None):
        """Build a dict of attribute values, respecting declaration order.

        The process is:
        - Handle 'orderless' attributes, overriding defaults with provided
            kwargs when applicable
        - Handle ordered attributes, overriding them with provided kwargs when
            applicable; the current list of computed attributes is available
            to the currently processed object.
        """
        warnings.warn(
            "Usage of Factory.attributes() is deprecated.",
            DeprecationWarning,
            stacklevel=2,
        )
        declarations = cls._meta.pre_declarations.as_dict()
        declarations.update(extra or {})
        from . import helpers
        return helpers.make_factory(dict, **declarations)

    @classmethod
    def declarations(cls, extra_defs=None):
        """Retrieve a copy of the declared attributes.

        Args:
            extra_defs (dict): additional definitions to insert into the
                retrieved DeclarationDict.
        """
        warnings.warn(
            "Factory.declarations is deprecated; use Factory._meta.pre_declarations instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        decls = cls._meta.pre_declarations.as_dict()
        decls.update(extra_defs or {})
        return decls

    @classmethod
    def _adjust_kwargs(cls, **kwargs):
        """Extension point for custom kwargs adjustment."""
        return kwargs

    @classmethod
    def _generate(cls, strategy, params):
        """generate the object.

        Args:
            params (dict): attributes to use for generating the object
            strategy: the strategy to use
        """
        if cls._meta.abstract:
            raise errors.FactoryError(
                "Cannot generate instances of abstract factory %(f)s; "
                "Ensure %(f)s.Meta.model is set and %(f)s.Meta.abstract "
                "is either not set or False." % dict(f=cls.__name__))

        step = builder.StepBuilder(cls._meta, params, strategy)
        return step.build()

    @classmethod
    def _after_postgeneration(cls, instance, create, results=None):
        """Hook called after post-generation declarations have been handled.

        Args:
            instance (object): the generated object
            create (bool): whether the strategy was 'build' or 'create'
            results (dict or None): result of post-generation declarations
        """
        pass

    @classmethod
    def _build(cls, model_class, *args, **kwargs):
        """Actually build an instance of the model_class.

        Customization point, will be called once the full set of args and kwargs
        has been computed.

        Args:
            model_class (type): the class for which an instance should be
                built
            args (tuple): arguments to use when building the class
            kwargs (dict): keyword arguments to use when building the class
        """
        return model_class(*args, **kwargs)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Actually create an instance of the model_class.

        Customization point, will be called once the full set of args and kwargs
        has been computed.

        Args:
            model_class (type): the class for which an instance should be
                created
            args (tuple): arguments to use when creating the class
            kwargs (dict): keyword arguments to use when creating the class
        """
        return model_class(*args, **kwargs)

    @classmethod
    def build(cls, **kwargs):
        """Build an instance of the associated class, with overriden attrs."""
        return cls._generate(enums.BUILD_STRATEGY, kwargs)

    @classmethod
    def build_batch(cls, size, **kwargs):
        """Build a batch of instances of the given class, with overriden attrs.

        Args:
            size (int): the number of instances to build

        Returns:
            object list: the built instances
        """
        return [cls.build(**kwargs) for _ in range(size)]

    @classmethod
    def create(cls, **kwargs):
        """Create an instance of the associated class, with overriden attrs."""
        return cls._generate(enums.CREATE_STRATEGY, kwargs)

    @classmethod
    def create_batch(cls, size, **kwargs):
        """Create a batch of instances of the given class, with overriden attrs.

        Args:
            size (int): the number of instances to create

        Returns:
            object list: the created instances
        """
        return [cls.create(**kwargs) for _ in range(size)]

    @classmethod
    def stub(cls, **kwargs):
        """Retrieve a stub of the associated class, with overriden attrs.

        This will return an object whose attributes are those defined in this
        factory's declarations or in the extra kwargs.
        """
        return cls._generate(enums.STUB_STRATEGY, kwargs)

    @classmethod
    def stub_batch(cls, size, **kwargs):
        """Stub a batch of instances of the given class, with overriden attrs.

        Args:
            size (int): the number of instances to stub

        Returns:
            object list: the stubbed instances
        """
        return [cls.stub(**kwargs) for _ in range(size)]

    @classmethod
    def generate(cls, strategy, **kwargs):
        """Generate a new instance.

        The instance will be created with the given strategy (one of
        BUILD_STRATEGY, CREATE_STRATEGY, STUB_STRATEGY).

        Args:
            strategy (str): the strategy to use for generating the instance.

        Returns:
            object: the generated instance
        """
        assert strategy in (enums.STUB_STRATEGY, enums.BUILD_STRATEGY, enums.CREATE_STRATEGY)
        action = getattr(cls, strategy)
        return action(**kwargs)

    @classmethod
    def generate_batch(cls, strategy, size, **kwargs):
        """Generate a batch of instances.

        The instances will be created with the given strategy (one of
        BUILD_STRATEGY, CREATE_STRATEGY, STUB_STRATEGY).

        Args:
            strategy (str): the strategy to use for generating the instance.
            size (int): the number of instances to generate

        Returns:
            object list: the generated instances
        """
        assert strategy in (enums.STUB_STRATEGY, enums.BUILD_STRATEGY, enums.CREATE_STRATEGY)
        batch_action = getattr(cls, '%s_batch' % strategy)
        return batch_action(size, **kwargs)

    @classmethod
    def simple_generate(cls, create, **kwargs):
        """Generate a new instance.

        The instance will be either 'built' or 'created'.

        Args:
            create (bool): whether to 'build' or 'create' the instance.

        Returns:
            object: the generated instance
        """
        strategy = enums.CREATE_STRATEGY if create else enums.BUILD_STRATEGY
        return cls.generate(strategy, **kwargs)

    @classmethod
    def simple_generate_batch(cls, create, size, **kwargs):
        """Generate a batch of instances.

        These instances will be either 'built' or 'created'.

        Args:
            size (int): the number of instances to generate
            create (bool): whether to 'build' or 'create' the instances.

        Returns:
            object list: the generated instances
        """
        strategy = enums.CREATE_STRATEGY if create else enums.BUILD_STRATEGY
        return cls.generate_batch(strategy, size, **kwargs)

    @classmethod
    def auto_factory(
            cls,
            model,
            default_auto_fields=True,
            include_auto_fields=(),
            exclude_auto_fields=(),
            introspector_class=None,
            **field_overrides):
        """Introspect the target_model and build a factory for it.

        Args:
            target_model (type): the model for which a factory should be built
            for_fields (str list): name of fields to introspect on the model
            field_overrides (dict): extra declarations to include in the factory,
                e.g default values

        Returns:
            Factory subclass: the generated factory
        """
        factory_name = str('%sAutoFactory' % model.__name__)

        # work around variable shadowing
        _model = model
        _default_auto_fields = default_auto_fields
        _include_auto_fields = include_auto_fields
        _exclude_auto_fields = exclude_auto_fields
        _introspector_class = introspector_class

        class Meta:
            model = _model
            default_auto_fields = _default_auto_fields
            include_auto_fields = _include_auto_fields
            exclude_auto_fields = _exclude_auto_fields
            if _introspector_class:
                introspector_class = _introspector_class
        attrs = {}
        attrs.update(field_overrides)
        attrs['Meta'] = Meta
        # We want to force the name of the Factory subclass.
        factory_class = type(factory_name, (cls,), attrs)
        return factory_class


# Note: we're calling str() on the class name to avoid issues with Py2's type() expecting bytes
# instead of unicode.
Factory = FactoryMetaClass(str('Factory'), (BaseFactory,), {
    'Meta': BaseMeta,
    '__doc__': """Factory base with build and create support.

    This class has the ability to support multiple ORMs by using custom creation
    functions.
    """,
})


# Backwards compatibility
Factory.AssociatedClassError = errors.AssociatedClassError


class StubObject(object):
    """A generic container."""
    def __init__(self, **kwargs):
        for field, value in kwargs.items():
            setattr(self, field, value)


class StubFactory(Factory):

    class Meta:
        strategy = enums.STUB_STRATEGY
        model = StubObject

    @classmethod
    def build(cls, **kwargs):
        return cls.stub(**kwargs)

    @classmethod
    def create(cls, **kwargs):
        raise errors.UnsupportedStrategy()


class BaseDictFactory(Factory):
    """Factory for dictionary-like classes."""
    class Meta:
        abstract = True

    @classmethod
    def _build(cls, model_class, *args, **kwargs):
        if args:
            raise ValueError(
                "DictFactory %r does not support Meta.inline_args.", cls)
        return model_class(**kwargs)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        return cls._build(model_class, *args, **kwargs)


class DictFactory(BaseDictFactory):
    class Meta:
        model = dict


class BaseListFactory(Factory):
    """Factory for list-like classes."""
    class Meta:
        abstract = True

    @classmethod
    def _build(cls, model_class, *args, **kwargs):
        if args:
            raise ValueError(
                "ListFactory %r does not support Meta.inline_args.", cls)

        values = [v for k, v in sorted(kwargs.items())]
        return model_class(values)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        return cls._build(model_class, *args, **kwargs)


class ListFactory(BaseListFactory):
    class Meta:
        model = list


def use_strategy(new_strategy):
    """Force the use of a different strategy.

    This is an alternative to setting default_strategy in the class definition.
    """
    def wrapped_class(klass):
        klass._meta.strategy = new_strategy
        return klass
    return wrapped_class
