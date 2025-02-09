# -*- coding: utf-8 -*-
# Copyright: See the LICENSE file.

"""Tests for factory_boy/Django interactions."""

import os
from .compat import is_python2, unittest, mock


import django

# Setup Django as soon as possible
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tests.djapp.settings')

django.setup()
from django import test as django_test
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models as django_models
from django.test.runner import DiscoverRunner as DjangoTestSuiteRunner
from django.test import utils as django_test_utils
from django.db.models import signals
from django.utils import timezone
from .djapp import models


try:
    from PIL import Image
except ImportError:  # pragma: no cover
    # Try PIL alternate name
    try:
        import Image
    except ImportError:
        # OK, not installed
        Image = None


import factory
import factory.django
from factory.compat import BytesIO

from . import testdata


test_state = {}


def setUpModule():
    django_test_utils.setup_test_environment()
    runner = DjangoTestSuiteRunner()
    runner_state = runner.setup_databases()
    test_state.update({
        'runner': runner,
        'runner_state': runner_state,
    })


def tearDownModule():
    runner = test_state['runner']
    runner_state = test_state['runner_state']
    runner.teardown_databases(runner_state)
    django_test_utils.teardown_test_environment()


class StandardFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.StandardModel

    foo = factory.Sequence(lambda n: "foo%d" % n)


class StandardFactoryWithPKField(factory.django.DjangoModelFactory):
    class Meta:
        model = models.StandardModel
        django_get_or_create = ('pk',)

    foo = factory.Sequence(lambda n: "foo%d" % n)
    pk = None


class NonIntegerPkFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.NonIntegerPk

    foo = factory.Sequence(lambda n: "foo%d" % n)
    bar = ''


class MultifieldModelFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.MultifieldModel
        django_get_or_create = ['slug']

    text = factory.Faker('text')


class AbstractBaseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.AbstractBase
        abstract = True

    foo = factory.Sequence(lambda n: "foo%d" % n)


class ConcreteSonFactory(AbstractBaseFactory):
    class Meta:
        model = models.ConcreteSon


class AbstractSonFactory(AbstractBaseFactory):
    class Meta:
        model = models.AbstractSon


class ConcreteGrandSonFactory(AbstractBaseFactory):
    class Meta:
        model = models.ConcreteGrandSon


class WithFileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.WithFile

    if django is not None:
        afile = factory.django.FileField()


class WithImageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.WithImage

    if django is not None:
        animage = factory.django.ImageField()


class WithSignalsFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.WithSignals


class WithCustomManagerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = models.WithCustomManager

    foo = factory.Sequence(lambda n: "foo%d" % n)


class ModelTests(django_test.TestCase):
    def test_unset_model(self):
        class UnsetModelFactory(factory.django.DjangoModelFactory):
            pass

        self.assertRaises(factory.FactoryError, UnsetModelFactory.create)

    def test_cross_database(self):
        class OtherDBFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = models.StandardModel
                database = 'replica'

        obj = OtherDBFactory()
        self.assertFalse(models.StandardModel.objects.exists())
        self.assertEqual(obj, models.StandardModel.objects.using('replica').get())


class DjangoPkSequenceTestCase(django_test.TestCase):
    def setUp(self):
        super(DjangoPkSequenceTestCase, self).setUp()
        StandardFactory.reset_sequence()

    def test_pk_first(self):
        std = StandardFactory.build()
        self.assertEqual('foo0', std.foo)

    def test_pk_many(self):
        std1 = StandardFactory.build()
        std2 = StandardFactory.build()
        self.assertEqual('foo0', std1.foo)
        self.assertEqual('foo1', std2.foo)

    def test_pk_creation(self):
        std1 = StandardFactory.create()
        self.assertEqual('foo0', std1.foo)
        self.assertEqual(1, std1.pk)

        StandardFactory.reset_sequence()
        std2 = StandardFactory.create()
        self.assertEqual('foo0', std2.foo)
        self.assertEqual(2, std2.pk)

    def test_pk_force_value(self):
        std1 = StandardFactory.create(pk=10)
        self.assertEqual('foo0', std1.foo)  # sequence is unrelated to pk
        self.assertEqual(10, std1.pk)

        StandardFactory.reset_sequence()
        std2 = StandardFactory.create()
        self.assertEqual('foo0', std2.foo)
        self.assertEqual(11, std2.pk)


class DjangoGetOrCreateTests(django_test.TestCase):
    def test_simple_call(self):
        obj1 = MultifieldModelFactory(slug='slug1')
        obj2 = MultifieldModelFactory(slug='slug1')
        obj3 = MultifieldModelFactory(slug='alt')

        self.assertEqual(obj1, obj2)
        self.assertEqual(2, models.MultifieldModel.objects.count())

    def test_missing_arg(self):
        with self.assertRaises(factory.FactoryError):
            MultifieldModelFactory()

    def test_multicall(self):
        objs = MultifieldModelFactory.create_batch(6,
            slug=factory.Iterator(['main', 'alt']),
        )
        self.assertEqual(6, len(objs))
        self.assertEqual(2, len(set(objs)))
        self.assertEqual(2, models.MultifieldModel.objects.count())


class DjangoPkForceTestCase(django_test.TestCase):
    def setUp(self):
        super(DjangoPkForceTestCase, self).setUp()
        StandardFactoryWithPKField.reset_sequence()

    def test_no_pk(self):
        std = StandardFactoryWithPKField()
        self.assertIsNotNone(std.pk)
        self.assertEqual('foo0', std.foo)

    def test_force_pk(self):
        std = StandardFactoryWithPKField(pk=42)
        self.assertIsNotNone(std.pk)
        self.assertEqual('foo0', std.foo)

    def test_reuse_pk(self):
        std1 = StandardFactoryWithPKField(foo='bar')
        self.assertIsNotNone(std1.pk)

        std2 = StandardFactoryWithPKField(pk=std1.pk, foo='blah')
        self.assertEqual(std1.pk, std2.pk)
        self.assertEqual('bar', std2.foo)


class DjangoModelLoadingTestCase(django_test.TestCase):
    """Tests class Meta:
     model = 'app.Model' pattern."""

    def test_loading(self):
        class ExampleFactory(factory.DjangoModelFactory):
            class Meta:
                model = 'djapp.StandardModel'

        self.assertEqual(models.StandardModel, ExampleFactory._meta.get_model_class())

    def test_building(self):
        class ExampleFactory(factory.DjangoModelFactory):
            class Meta:
                model = 'djapp.StandardModel'

        e = ExampleFactory.build()
        self.assertEqual(models.StandardModel, e.__class__)

    def test_inherited_loading(self):
        """Proper loading of a model within 'child' factories.

        See https://github.com/FactoryBoy/factory_boy/issues/109.
        """
        class ExampleFactory(factory.DjangoModelFactory):
            class Meta:
                model = 'djapp.StandardModel'

        class Example2Factory(ExampleFactory):
            pass

        e = Example2Factory.build()
        self.assertEqual(models.StandardModel, e.__class__)

    def test_inherited_loading_and_sequence(self):
        """Proper loading of a model within 'child' factories.

        See https://github.com/FactoryBoy/factory_boy/issues/109.
        """
        class ExampleFactory(factory.DjangoModelFactory):
            class Meta:
                model = 'djapp.StandardModel'

            foo = factory.Sequence(lambda n: n)

        class Example2Factory(ExampleFactory):
            class Meta:
                model = 'djapp.StandardSon'

        e1 = ExampleFactory.build()
        e2 = Example2Factory.build()
        e3 = ExampleFactory.build()
        self.assertEqual(models.StandardModel, e1.__class__)
        self.assertEqual(models.StandardSon, e2.__class__)
        self.assertEqual(models.StandardModel, e3.__class__)
        self.assertEqual(0, e1.foo)
        self.assertEqual(1, e2.foo)
        self.assertEqual(2, e3.foo)


class DjangoNonIntegerPkTestCase(django_test.TestCase):
    def setUp(self):
        super(DjangoNonIntegerPkTestCase, self).setUp()
        NonIntegerPkFactory.reset_sequence()

    def test_first(self):
        nonint = NonIntegerPkFactory.build()
        self.assertEqual('foo0', nonint.foo)

    def test_many(self):
        nonint1 = NonIntegerPkFactory.build()
        nonint2 = NonIntegerPkFactory.build()

        self.assertEqual('foo0', nonint1.foo)
        self.assertEqual('foo1', nonint2.foo)

    def test_creation(self):
        nonint1 = NonIntegerPkFactory.create()
        self.assertEqual('foo0', nonint1.foo)
        self.assertEqual('foo0', nonint1.pk)

        NonIntegerPkFactory.reset_sequence()
        nonint2 = NonIntegerPkFactory.build()
        self.assertEqual('foo0', nonint2.foo)

    def test_force_pk(self):
        nonint1 = NonIntegerPkFactory.create(pk='foo10')
        self.assertEqual('foo10', nonint1.foo)
        self.assertEqual('foo10', nonint1.pk)

        NonIntegerPkFactory.reset_sequence()
        nonint2 = NonIntegerPkFactory.create()
        self.assertEqual('foo0', nonint2.foo)
        self.assertEqual('foo0', nonint2.pk)


class DjangoAbstractBaseSequenceTestCase(django_test.TestCase):
    def test_auto_sequence(self):
        """The sequence of the concrete son of an abstract model should be autonomous."""
        obj = ConcreteSonFactory()
        self.assertEqual(1, obj.pk)

    def test_auto_sequence(self):
        """The sequence of the concrete grandson of an abstract model should be autonomous."""
        obj = ConcreteGrandSonFactory()
        self.assertEqual(1, obj.pk)

    def test_optional_abstract(self):
        """Users need not describe the factory for an abstract model as abstract."""
        class AbstractBaseFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = models.AbstractBase

            foo = factory.Sequence(lambda n: "foo%d" % n)

        class ConcreteSonFactory(AbstractBaseFactory):
            class Meta:
                model = models.ConcreteSon

        obj = ConcreteSonFactory()
        self.assertEqual(1, obj.pk)
        self.assertEqual("foo0", obj.foo)


class DjangoRelatedFieldTestCase(django_test.TestCase):

    @classmethod
    def setUpClass(cls):
        super(DjangoRelatedFieldTestCase, cls).setUpClass()
        class PointedFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = models.PointedModel
            foo = 'foo'

        class PointerFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = models.PointerModel
            bar = 'bar'
            pointed = factory.SubFactory(PointedFactory, foo='new_foo')

        class PointedRelatedFactory(PointedFactory):
            pointer = factory.RelatedFactory(PointerFactory, 'pointed')

        class PointerExtraFactory(PointerFactory):
            pointed__foo = 'extra_new_foo'

        class PointedRelatedExtraFactory(PointedRelatedFactory):
            pointer__bar = 'extra_new_bar'

        cls.PointedFactory = PointedFactory
        cls.PointerFactory = PointerFactory
        cls.PointedRelatedFactory = PointedRelatedFactory
        cls.PointerExtraFactory = PointerExtraFactory
        cls.PointedRelatedExtraFactory = PointedRelatedExtraFactory

    def test_create_pointed(self):
        pointed = self.PointedFactory()
        self.assertEqual(pointed, models.PointedModel.objects.get())
        self.assertEqual(pointed.foo, 'foo')

    def test_create_pointer(self):
        pointer = self.PointerFactory()
        self.assertEqual(pointer.pointed, models.PointedModel.objects.get())
        self.assertEqual(pointer.pointed.foo, 'new_foo')

    def test_create_pointer_with_deep_context(self):
        pointer = self.PointerFactory(pointed__foo='new_new_foo')
        self.assertEqual(pointer, models.PointerModel.objects.get())
        self.assertEqual(pointer.bar, 'bar')
        self.assertEqual(pointer.pointed, models.PointedModel.objects.get())
        self.assertEqual(pointer.pointed.foo, 'new_new_foo')

    def test_create_pointed_related(self):
        pointed = self.PointedRelatedFactory()
        self.assertEqual(pointed, models.PointedModel.objects.get())
        self.assertEqual(pointed.foo, 'foo')
        self.assertEqual(pointed.pointer, models.PointerModel.objects.get())
        self.assertEqual(pointed.pointer.bar, 'bar')

    def test_create_pointed_related_with_deep_context(self):
        pointed = self.PointedRelatedFactory(pointer__bar='new_new_bar')
        self.assertEqual(pointed, models.PointedModel.objects.get())
        self.assertEqual(pointed.foo, 'foo')
        self.assertEqual(pointed.pointer, models.PointerModel.objects.get())
        self.assertEqual(pointed.pointer.bar, 'new_new_bar')

    def test_create_pointer_extra(self):
        pointer = self.PointerExtraFactory()
        self.assertEqual(pointer, models.PointerModel.objects.get())
        self.assertEqual(pointer.bar, 'bar')
        self.assertEqual(pointer.pointed, models.PointedModel.objects.get())
        self.assertEqual(pointer.pointed.foo, 'extra_new_foo')

    def test_create_pointed_related_extra(self):
        pointed = self.PointedRelatedExtraFactory()
        self.assertEqual(pointed, models.PointedModel.objects.get())
        self.assertEqual(pointed.foo, 'foo')
        self.assertEqual(pointed.pointer, models.PointerModel.objects.get())
        self.assertEqual(pointed.pointer.bar, 'extra_new_bar')


class DjangoFileFieldTestCase(unittest.TestCase):

    def tearDown(self):
        super(DjangoFileFieldTestCase, self).tearDown()
        for path in os.listdir(models.WITHFILE_UPLOAD_DIR):
            # Remove temporary files written during tests.
            os.unlink(os.path.join(models.WITHFILE_UPLOAD_DIR, path))

    def test_default_build(self):
        o = WithFileFactory.build()
        self.assertIsNone(o.pk)
        self.assertEqual(b'', o.afile.read())
        self.assertEqual('example.dat', o.afile.name)

        o.save()
        self.assertEqual('django/example.dat', o.afile.name)

    def test_default_create(self):
        o = WithFileFactory.create()
        self.assertIsNotNone(o.pk)
        self.assertEqual(b'', o.afile.read())
        self.assertEqual('django/example.dat', o.afile.name)

    def test_with_content(self):
        o = WithFileFactory.build(afile__data='foo')
        self.assertIsNone(o.pk)

        # Django only allocates the full path on save()
        o.save()
        self.assertEqual(b'foo', o.afile.read())
        self.assertEqual('django/example.dat', o.afile.name)

    def test_with_file(self):
        with open(testdata.TESTFILE_PATH, 'rb') as f:
            o = WithFileFactory.build(afile__from_file=f)
            o.save()

        self.assertEqual(b'example_data\n', o.afile.read())
        self.assertEqual('django/example.data', o.afile.name)

    def test_with_path(self):
        o = WithFileFactory.build(afile__from_path=testdata.TESTFILE_PATH)
        self.assertIsNone(o.pk)

        # Django only allocates the full path on save()
        o.save()
        self.assertEqual(b'example_data\n', o.afile.read())
        self.assertEqual('django/example.data', o.afile.name)

    def test_with_file_empty_path(self):
        with open(testdata.TESTFILE_PATH, 'rb') as f:
            o = WithFileFactory.build(
                afile__from_file=f,
                afile__from_path=''
            )
            # Django only allocates the full path on save()
            o.save()

        self.assertEqual(b'example_data\n', o.afile.read())
        self.assertEqual('django/example.data', o.afile.name)

    def test_with_path_empty_file(self):
        o = WithFileFactory.build(
            afile__from_path=testdata.TESTFILE_PATH,
            afile__from_file=None,
        )
        self.assertIsNone(o.pk)

        # Django only allocates the full path on save()
        o.save()
        self.assertEqual(b'example_data\n', o.afile.read())
        self.assertEqual('django/example.data', o.afile.name)

    def test_error_both_file_and_path(self):
        self.assertRaises(ValueError, WithFileFactory.build,
            afile__from_file='fakefile',
            afile__from_path=testdata.TESTFILE_PATH,
        )

    def test_override_filename_with_path(self):
        o = WithFileFactory.build(
            afile__from_path=testdata.TESTFILE_PATH,
            afile__filename='example.foo',
        )
        self.assertIsNone(o.pk)

        # Django only allocates the full path on save()
        o.save()
        self.assertEqual(b'example_data\n', o.afile.read())
        self.assertEqual('django/example.foo', o.afile.name)

    def test_existing_file(self):
        o1 = WithFileFactory.build(afile__from_path=testdata.TESTFILE_PATH)
        o1.save()
        self.assertEqual('django/example.data', o1.afile.name)

        o2 = WithFileFactory.build(afile__from_file=o1.afile)
        self.assertIsNone(o2.pk)
        o2.save()

        self.assertEqual(b'example_data\n', o2.afile.read())
        self.assertNotEqual('django/example.data', o2.afile.name)
        self.assertRegexpMatches(o2.afile.name, r'django/example_\w+.data')

    def test_no_file(self):
        o = WithFileFactory.build(afile=None)
        self.assertIsNone(o.pk)
        self.assertFalse(o.afile)


class DjangoParamsTestCase(django_test.TestCase):

    def test_undeclared_fields(self):
        class WithDefaultValueFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = models.WithDefaultValue

            class Params:
                with_bar = factory.Trait(
                    foo='bar',
                )

        o = WithDefaultValueFactory()
        self.assertEqual('', o.foo)

    def test_pointing_with_traits_using_same_name(self):
        class PointedFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = models.PointedModel

            class Params:
                with_bar = factory.Trait(
                    foo='bar',
                )

        class PointerFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = models.PointerModel
            pointed = factory.SubFactory(PointedFactory)

            class Params:
                with_bar = factory.Trait(
                    bar='bar',
                    pointed__with_bar=True,
                )

        o = PointerFactory(with_bar=True)
        self.assertEqual('bar', o.bar)
        self.assertEqual('bar', o.pointed.foo)


class DjangoFakerTestCase(unittest.TestCase):
    def test_random(self):
        class StandardModelFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = models.StandardModel
            foo = factory.Faker('pystr')

        o1 = StandardModelFactory()
        o2 = StandardModelFactory()
        self.assertNotEqual(o1.foo, o2.foo)


@unittest.skipIf(Image is None, "PIL not installed.")
class DjangoImageFieldTestCase(unittest.TestCase):

    def tearDown(self):
        super(DjangoImageFieldTestCase, self).tearDown()
        for path in os.listdir(models.WITHFILE_UPLOAD_DIR):
            # Remove temporary files written during tests.
            os.unlink(os.path.join(models.WITHFILE_UPLOAD_DIR, path))

    def test_default_build(self):
        o = WithImageFactory.build()
        self.assertIsNone(o.pk)
        o.save()

        self.assertEqual(100, o.animage.width)
        self.assertEqual(100, o.animage.height)
        self.assertEqual('django/example.jpg', o.animage.name)

    def test_default_create(self):
        o = WithImageFactory.create()
        self.assertIsNotNone(o.pk)
        o.save()

        self.assertEqual(100, o.animage.width)
        self.assertEqual(100, o.animage.height)
        self.assertEqual('django/example.jpg', o.animage.name)

    def test_complex_create(self):
        o = WithImageFactory.create(
            size=10,
            animage__filename=factory.Sequence(lambda n: 'img%d.jpg' % n),
            __sequence=42,
            animage__width=factory.SelfAttribute('..size'),
            animage__height=factory.SelfAttribute('width'),
        )
        self.assertIsNotNone(o.pk)
        self.assertEqual('django/img42.jpg', o.animage.name)

    def test_with_content(self):
        o = WithImageFactory.build(animage__width=13, animage__color='red')
        self.assertIsNone(o.pk)
        o.save()

        self.assertEqual(13, o.animage.width)
        self.assertEqual(13, o.animage.height)
        self.assertEqual('django/example.jpg', o.animage.name)

        i = Image.open(os.path.join(settings.MEDIA_ROOT, o.animage.name))
        colors = i.getcolors()
        # 169 pixels with rgb(254, 0, 0)
        self.assertEqual([(169, (254, 0, 0))], colors)
        self.assertEqual('JPEG', i.format)

    def test_gif(self):
        o = WithImageFactory.build(animage__width=13, animage__color='blue', animage__format='GIF')
        self.assertIsNone(o.pk)
        o.save()

        self.assertEqual(13, o.animage.width)
        self.assertEqual(13, o.animage.height)
        self.assertEqual('django/example.jpg', o.animage.name)

        i = Image.open(os.path.join(settings.MEDIA_ROOT, o.animage.name))
        colors = i.convert('RGB').getcolors()
        # 169 pixels with rgb(0, 0, 255)
        self.assertEqual([(169, (0, 0, 255))], colors)
        self.assertEqual('GIF', i.format)

    def test_with_file(self):
        with open(testdata.TESTIMAGE_PATH, 'rb') as f:
            o = WithImageFactory.build(animage__from_file=f)
            o.save()

        # Image file for a 42x42 green jpeg: 301 bytes long.
        self.assertEqual(301, len(o.animage.read()))
        self.assertEqual('django/example.jpeg', o.animage.name)

    def test_with_path(self):
        o = WithImageFactory.build(animage__from_path=testdata.TESTIMAGE_PATH)
        self.assertIsNone(o.pk)
        o.save()

        # Image file for a 42x42 green jpeg: 301 bytes long.
        self.assertEqual(301, len(o.animage.read()))
        self.assertEqual('django/example.jpeg', o.animage.name)

    def test_with_file_empty_path(self):
        with open(testdata.TESTIMAGE_PATH, 'rb') as f:
            o = WithImageFactory.build(
                animage__from_file=f,
                animage__from_path=''
            )
            o.save()

        # Image file for a 42x42 green jpeg: 301 bytes long.
        self.assertEqual(301, len(o.animage.read()))
        self.assertEqual('django/example.jpeg', o.animage.name)

    def test_with_path_empty_file(self):
        o = WithImageFactory.build(
            animage__from_path=testdata.TESTIMAGE_PATH,
            animage__from_file=None,
        )
        self.assertIsNone(o.pk)
        o.save()

        # Image file for a 42x42 green jpeg: 301 bytes long.
        self.assertEqual(301, len(o.animage.read()))
        self.assertEqual('django/example.jpeg', o.animage.name)

    def test_error_both_file_and_path(self):
        self.assertRaises(ValueError, WithImageFactory.build,
            animage__from_file='fakefile',
            animage__from_path=testdata.TESTIMAGE_PATH,
        )

    def test_override_filename_with_path(self):
        o = WithImageFactory.build(
            animage__from_path=testdata.TESTIMAGE_PATH,
            animage__filename='example.foo',
        )
        self.assertIsNone(o.pk)
        o.save()

        # Image file for a 42x42 green jpeg: 301 bytes long.
        self.assertEqual(301, len(o.animage.read()))
        self.assertEqual('django/example.foo', o.animage.name)

    def test_existing_file(self):
        o1 = WithImageFactory.build(animage__from_path=testdata.TESTIMAGE_PATH)
        o1.save()

        o2 = WithImageFactory.build(animage__from_file=o1.animage)
        self.assertIsNone(o2.pk)
        o2.save()

        # Image file for a 42x42 green jpeg: 301 bytes long.
        self.assertEqual(301, len(o2.animage.read()))
        self.assertNotEqual('django/example.jpeg', o2.animage.name)
        self.assertRegexpMatches(o2.animage.name, r'django/example_\w+.jpeg')

    def test_no_file(self):
        o = WithImageFactory.build(animage=None)
        self.assertIsNone(o.pk)
        self.assertFalse(o.animage)

    def _img_test_func(self):
        img = Image.new('RGB', (32,32), 'blue')
        img_io = BytesIO()
        img.save(img_io, format='JPEG')
        img_io.seek(0)
        return img_io

    def test_with_func(self):
        o = WithImageFactory.build(animage__from_func=self._img_test_func)
        self.assertIsNone(o.pk)
        i = Image.open(o.animage.file)
        self.assertEqual('JPEG', i.format)
        self.assertEqual(32, i.width)
        self.assertEqual(32, i.height)


class PreventSignalsTestCase(unittest.TestCase):
    def setUp(self):
        self.handlers = mock.MagicMock()

        signals.pre_init.connect(self.handlers.pre_init)
        signals.pre_save.connect(self.handlers.pre_save)
        signals.post_save.connect(self.handlers.post_save)

    def tearDown(self):
        signals.pre_init.disconnect(self.handlers.pre_init)
        signals.pre_save.disconnect(self.handlers.pre_save)
        signals.post_save.disconnect(self.handlers.post_save)

    def assertSignalsReactivated(self):
        WithSignalsFactory()

        self.assertEqual(self.handlers.pre_save.call_count, 1)
        self.assertEqual(self.handlers.post_save.call_count, 1)

    def test_context_manager(self):
        with factory.django.mute_signals(signals.pre_save, signals.post_save):
            WithSignalsFactory()

        self.assertEqual(self.handlers.pre_init.call_count, 1)
        self.assertFalse(self.handlers.pre_save.called)
        self.assertFalse(self.handlers.post_save.called)

        self.assertSignalsReactivated()

    def test_signal_cache(self):
        with factory.django.mute_signals(signals.pre_save, signals.post_save):
            signals.post_save.connect(self.handlers.mute_block_receiver)
            WithSignalsFactory()

        self.assertTrue(self.handlers.mute_block_receiver.call_count, 1)
        self.assertEqual(self.handlers.pre_init.call_count, 1)
        self.assertFalse(self.handlers.pre_save.called)
        self.assertFalse(self.handlers.post_save.called)

        self.assertSignalsReactivated()
        self.assertTrue(self.handlers.mute_block_receiver.call_count, 1)

    def test_class_decorator(self):
        @factory.django.mute_signals(signals.pre_save, signals.post_save)
        class WithSignalsDecoratedFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = models.WithSignals

        WithSignalsDecoratedFactory()

        self.assertEqual(self.handlers.pre_init.call_count, 1)
        self.assertFalse(self.handlers.pre_save.called)
        self.assertFalse(self.handlers.post_save.called)

        self.assertSignalsReactivated()

    def test_class_decorator_with_subfactory(self):
        @factory.django.mute_signals(signals.pre_save, signals.post_save)
        class WithSignalsDecoratedFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = models.WithSignals

            @factory.post_generation
            def post(obj, create, extracted, **kwargs):
                if not extracted:
                    WithSignalsDecoratedFactory.create(post=42)

        # This will disable the signals (twice), create two objects,
        # and reactivate the signals.
        WithSignalsDecoratedFactory()

        self.assertEqual(self.handlers.pre_init.call_count, 2)
        self.assertFalse(self.handlers.pre_save.called)
        self.assertFalse(self.handlers.post_save.called)

        self.assertSignalsReactivated()

    def test_class_decorator_build(self):
        @factory.django.mute_signals(signals.pre_save, signals.post_save)
        class WithSignalsDecoratedFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = models.WithSignals

        WithSignalsDecoratedFactory.build()

        self.assertEqual(self.handlers.pre_init.call_count, 1)
        self.assertFalse(self.handlers.pre_save.called)
        self.assertFalse(self.handlers.post_save.called)

        self.assertSignalsReactivated()

    def test_function_decorator(self):
        @factory.django.mute_signals(signals.pre_save, signals.post_save)
        def foo():
            WithSignalsFactory()

        foo()

        self.assertEqual(self.handlers.pre_init.call_count, 1)
        self.assertFalse(self.handlers.pre_save.called)
        self.assertFalse(self.handlers.post_save.called)

        self.assertSignalsReactivated()

    def test_classmethod_decorator(self):
        class Foo(object):
            @classmethod
            @factory.django.mute_signals(signals.pre_save, signals.post_save)
            def generate(cls):
                WithSignalsFactory()

        Foo.generate()

        self.assertEqual(self.handlers.pre_init.call_count, 1)
        self.assertFalse(self.handlers.pre_save.called)
        self.assertFalse(self.handlers.post_save.called)

        self.assertSignalsReactivated()


class DjangoCustomManagerTestCase(unittest.TestCase):

    def test_extra_args(self):
        # Our CustomManager will remove the 'arg=' argument.
        model = WithCustomManagerFactory(arg='foo')

    def test_with_manager_on_abstract(self):
        class ObjFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = models.FromAbstractWithCustomManager

        # Our CustomManager will remove the 'arg=' argument,
        # invalid for the actual model.
        ObjFactory.create(arg='invalid')

@unittest.skipIf(django is None, "Django not installed.")
class AutoDjangoFactoryTestCase(unittest.TestCase):

    def test_auto_factory(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(models.ComprehensiveMultiFieldModel)

        obj = AutoFactory.create()
        obj.full_clean()
        self.assertLess(-1000, obj.int)
        self.assertLess(obj.int, 1000)
        self.assertEqual(4, len(obj.char))
        self.assertIsNotNone(obj.datetime.year)
        self.assertIsNotNone(obj.boolean)
        self.assertIsNone(obj.nullboolean)

        now = timezone.now()
        self.assertIsNotNone(obj.datetime_auto_now)
        self.assertIsNotNone(obj.datetime_auto_now_add)
        self.assertLessEqual((now - obj.datetime_auto_now).total_seconds(), 5.0)
        self.assertLessEqual((now - obj.datetime_auto_now_add).total_seconds(), 5.0)


    def test_auto_factory_with_override(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(models.ComprehensiveMultiFieldModel,
            int=factory.Sequence(lambda n: n * 2),
        )
        obj1 = AutoFactory.create()
        obj1.full_clean()
        obj2 = AutoFactory.create()
        obj2.full_clean()
        obj3 = AutoFactory.create()
        obj3.full_clean()

        self.assertEqual(0, obj1.int)
        self.assertEqual(2, obj2.int)
        self.assertEqual(4, obj3.int)

    def test_auto_factory_partial(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(
            model=models.ComprehensiveMultiFieldModel,
            default_auto_fields=False,
            include_auto_fields=[
                'int',
                'dec', 'bigint', 'smallposint', 'float', 'smallint', 'duration',
                'date', 'datetime', 'time',
                'ipv4', 'ipv6', 'ipany',
                'uuid',
            ])

        obj = AutoFactory.create(posint=0)
        self.assertLessEqual(-1000, obj.int)
        self.assertLess(obj.int, 1000)
        self.assertEqual('', obj.char)
        self.assertIsNotNone(obj.datetime.year)
        with self.assertRaises(ValidationError):
            obj.full_clean()

    def test_choices(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(models.ChoicesModel)
        for i in range(30):
            obj = AutoFactory.create()
            obj.full_clean() # will validate that choices are selected
            self.assertIsNone(obj.char_opt)
            self.assertIsNone(obj.int_opt)

    def test_foreignkey(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(models.ForeignKeyModel)
        obj = AutoFactory.create()
        obj.full_clean()

        self.assertEqual(20, len(obj.name))
        self.assertIsNotNone(obj.target)

    def test_foreignkey_reverse(self):
        class AutoFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = models.ForeignKeyModel
                default_auto_fields = True

        obj = AutoFactory.create()
        obj.full_clean()
        self.assertEqual(20, len(obj.name))
        self.assertIsNotNone(obj.target)

    def test_one_to_one(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(models.OneToOneModel)

        obj = AutoFactory.create()
        obj.full_clean()
        self.assertIsNotNone(obj.relates_to_req)
        self.assertIsNotNone(obj.relates_to_req.target)
        self.assertIsNone(obj.relates_to_opt)

    def test_manytomany(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(models.ManyToManySourceModel)
        obj = AutoFactory.create()
        obj.full_clean()
        self.assertEqual(20, len(obj.name))
        self.assertEqual(0, len(obj.targets_opt.all()))
        self.assertLessEqual(2, len(obj.targets_req.all()))

    def test_relationship_reverse(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(models.ComprehensiveMultiFieldModel)
        obj = AutoFactory.create()
        obj.full_clean()
        self.assertEqual(0, obj.foreignkeymodel_set.count())
        self.assertEqual(0, obj.manytomanysourcemodel_set.count())
        self.assertEqual(0, obj.manytomanythroughmodel_set.count())
        self.assertEqual(0, obj.manytomanywiththroughsourcemodel_set.count())

    def test_manytomany_with_through(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(
            models.ManyToManyWithThroughSourceModel,
            include_auto_fields=['targets'])

        obj = AutoFactory.create()
        obj.full_clean()
        self.assertEqual(20, len(obj.name))
        self.assertEqual(1, len(obj.targets.all()))

    def test_foreignkey_cycle(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(
            models.CycleCModel,
            b_fkey__a_fkey__c_fkey=None,
        )

        obj = AutoFactory.create()
        obj.full_clean()
        self.assertIsNotNone(obj.b_fkey)
        self.assertIsNotNone(obj.b_fkey.a_fkey)
        self.assertIsNone(obj.b_fkey.a_fkey.c_fkey)

    def test_optional_field(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(models.OptionalModel)
        obj = AutoFactory.create()
        obj.full_clean()
        self.assertEqual(obj.CHAR_LEN, len(obj.char_req))
        self.assertEqual('', obj.char_blank)
        self.assertEqual(obj.CHAR_LEN, len(obj.char_null))
        self.assertIsNone(obj.char_blank_null)
        self.assertEqual(obj._meta.get_field('char_blank_null_default').default, obj.char_blank_null_default)

        self.assertIsNotNone(obj.int_req)
        self.assertIsInstance(obj.int_blank, int)
        self.assertIsInstance(obj.int_null, int)
        self.assertIsNone(obj.int_blank_null)
        self.assertEqual(obj._meta.get_field('int_blank_null_default').default, obj.int_blank_null_default)

    def test_optional_field_include(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(
            model=models.OptionalModel,
            include_auto_fields=['char_blank_null'])
        obj = AutoFactory.create()
        obj.full_clean()
        self.assertEqual(obj.CHAR_LEN, len(obj.char_blank_null))

    def test_optional_field_include_all(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(
            model=models.OptionalModel,
            introspector_class=factory.django.AllFieldsDjangoIntrospector)
        obj = AutoFactory.create()
        obj.full_clean()

        self.assertEqual(obj.CHAR_LEN, len(obj.char_req))
        self.assertEqual(obj.CHAR_LEN, len(obj.char_blank))
        self.assertEqual(obj.CHAR_LEN, len(obj.char_null))
        self.assertEqual(obj.CHAR_LEN, len(obj.char_blank_null))
        self.assertEqual(obj.CHAR_LEN, len(obj.char_blank_null_default))

        self.assertIsInstance(obj.int_req, int)
        self.assertIsInstance(obj.int_blank, int)
        self.assertIsInstance(obj.int_null, int)
        self.assertIsInstance(obj.int_blank_null, int)
        self.assertIsInstance(obj.int_blank_null_default, int)

    def test_optional_field_include_unknown(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(
            model=models.OptionalModel,
            include_auto_fields=['nonexistentfield'])
        obj = AutoFactory.create()
        obj.full_clean()

    def test_required_field_exclude_int(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(
            model=models.OptionalModel,
            exclude_auto_fields=['int_req'])

        obj = AutoFactory.build()
        with self.assertRaises(ValidationError):
            obj.full_clean()

    def test_required_field_exclude_char(self):
        # because of the special blank/null handling by char/text etc, clean() won't actually fail
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(
            model=models.OptionalModel,
            exclude_auto_fields=['char_req'])

        obj = AutoFactory.build()
        with self.assertRaises(ValidationError):
            obj.full_clean()

    def test_optional_field_exclude_unknown(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(
            model=models.OptionalModel,
            exclude_auto_fields=['nonexistentfield'])
        obj = AutoFactory.create()
        obj.full_clean()

    def test_order_with_respect_to(self):
        AutoFactory = factory.django.DjangoModelFactory.auto_factory(models.OrderWithRespectTo)
        obj = AutoFactory.create()
        obj.full_clean()

    def test_inheritance_abstract(self):
        models.ParentModel._meta.get_field('fk').related_model.objects.all().delete()
        models.ChildModel._meta.get_field('fk2').related_model.objects.all().delete()
        models.ChildModel.objects.all().delete()

        AutoFactory = factory.django.DjangoModelFactory.auto_factory(models.ChildModel)
        obj = AutoFactory.create()
        obj.full_clean()

        self.assertEqual(20, len(obj.char_req))
        self.assertEqual(20, len(obj.char_req2))
        self.assertIsNone(obj.char_opt)
        self.assertIsNone(obj.char_opt2)

        self.assertIsInstance(obj.int_req, int)
        self.assertIsInstance(obj.int_req2, int)
        self.assertIsNone(obj.int_opt)
        self.assertIsNone(obj.int_opt2)

        self.assertEqual(1, models.ChildModel._meta.get_field('fk').related_model.objects.count())
        self.assertEqual(1, models.ChildModel._meta.get_field('fk2').related_model.objects.count())
        self.assertEqual(1, models.ChildModel.objects.count())

    def test_inheritance_multitable(self):
        models.MultiTableParentModel._meta.get_field('fk').related_model.objects.all().delete()
        models.MultiTableParentModel.objects.all().delete()
        models.MultiTableChildModel._meta.get_field('fk2').related_model.objects.all().delete()
        models.MultiTableChildModel.objects.all().delete()

        AutoFactory = factory.django.DjangoModelFactory.auto_factory(models.MultiTableChildModel)
        obj = AutoFactory.create()
        obj.full_clean()

        self.assertEqual(20, len(obj.char_req))
        self.assertEqual(20, len(obj.char_req2))
        self.assertIsNone(obj.char_opt)
        self.assertIsNone(obj.char_opt2)

        self.assertIsInstance(obj.int_req, int)
        self.assertIsInstance(obj.int_req2, int)
        self.assertIsNone(obj.int_opt)
        self.assertIsNone(obj.int_opt2)

        self.assertEqual(1, models.MultiTableParentModel._meta.get_field('fk').related_model.objects.count())
        self.assertEqual(1, models.MultiTableChildModel._meta.get_field('fk2').related_model.objects.count())
        self.assertEqual(1, models.MultiTableParentModel.objects.count())
        self.assertEqual(1, models.MultiTableChildModel.objects.count())

    def test_factory_meta_inheritance_abstract(self):
        """
        Descendant factories inheriting from abstract factories should inherit all autofactory meta attributes
        """

        class BaseFactory(factory.django.DjangoModelFactory):
            char_blank_null = '99 bottles of beer'

            class Meta:
                abstract = True
                model = models.OptionalModel
                default_auto_fields = True
                include_auto_fields = ['char_blank_null', 'int_blank_null']
                exclude_auto_fields = ['int_req', 'char_req']

        class ChildFactory(BaseFactory):
            char_blank_null = '97 bottles of beer'

            class Meta:
                abstract = True
                include_auto_fields = ['int_blank_null', 'int_req', 'int_null']
                exclude_auto_fields = ['char_blank_null']

        class GrandChildFactory(ChildFactory):
            class Meta:
                include_auto_fields = ['char_blank_null']
                exclude_auto_fields = ['int_blank_null']

        obj = GrandChildFactory.build()

        # explicit field defs always win over auto field defs
        self.assertEqual(obj.char_blank_null, ChildFactory.char_blank_null)

        # include overridding inherited exclude
        self.assertIsNotNone(obj.int_req)

        # exclude overriding inherited include
        self.assertNotIn('int_blank_null', GrandChildFactory._meta.declarations)
        self.assertIsNone(obj.int_blank_null)

        # inherited exclude
        self.assertNotIn('char_req', GrandChildFactory._meta.declarations)
        self.assertEqual(obj.char_req, '')

        # inherited include
        self.assertIsNotNone(obj.int_null)

    def test_factory_meta_inheritance_concrete(self):
        """
        Descendant factories inheriting from concrete factories should inherit autofactory meta attributes
        but won't remove concrete field instantiations (excluding a previous included field won't work)
        """

        class BaseFactory(factory.django.DjangoModelFactory):
            char_blank_null = '99 bottles of beer'

            class Meta:
                abstract = True
                model = models.OptionalModel
                default_auto_fields = True
                include_auto_fields = ['char_blank_null', 'int_blank_null']
                exclude_auto_fields = ['int_req', 'char_req']

        class ChildFactory(BaseFactory):
            char_blank_null = '97 bottles of beer'

            class Meta:
                abstract = False
                include_auto_fields = ['int_blank_null', 'int_req', 'int_null']
                exclude_auto_fields = ['char_blank_null']

        obj = ChildFactory.build()
        # explicit field defs always win over auto field defs
        self.assertEqual(obj.char_blank_null, ChildFactory.char_blank_null)

        # include overridding inherited exclude
        self.assertIsNotNone(obj.int_req)

        # inherited include
        self.assertIn('int_blank_null', ChildFactory._meta.declarations)
        self.assertIsNotNone(obj.int_blank_null)

        # inherited exclude
        self.assertNotIn('char_req', ChildFactory._meta.declarations)
        self.assertEqual(obj.char_req, '')

        # explicit include
        self.assertIsNotNone(obj.int_null)

        class GrandChildFactory(ChildFactory):
            class Meta:
                include_auto_fields = ['char_blank_null']
                exclude_auto_fields = ['int_blank_null']

        obj = GrandChildFactory.build()

        # explicit field defs always win over auto field defs
        self.assertEqual(obj.char_blank_null, ChildFactory.char_blank_null)

        # include overridding inherited exclude
        self.assertIsNotNone(obj.int_req)

        # exclude won't override inherited include doesn't apply if ancestor include was not abstract
        self.assertIn('int_blank_null', GrandChildFactory._meta.declarations)
        self.assertIsNotNone(obj.int_blank_null)

        # inherited exclude
        self.assertNotIn('char_req', GrandChildFactory._meta.declarations)
        self.assertEqual(obj.char_req, '')

        # inherited include
        self.assertIsNotNone(obj.int_null)

    # TODO: Test GenericForeignKey
    # TODO: Test GenericForeignKey reverse (GenericRelation?)

if __name__ == '__main__':  # pragma: no cover
    unittest.main()
