# -*- coding: utf-8 -*-
# Copyright: See the LICENSE file.


"""Helpers for testing django apps."""

import os.path

try:
    from PIL import Image
except ImportError:
    try:
        import Image
    except ImportError:
        Image = None

import django
from django.conf import settings
from django.db import models


class StandardModel(models.Model):
    foo = models.CharField(max_length=20)


class NonIntegerPk(models.Model):
    foo = models.CharField(max_length=20, primary_key=True)
    bar = models.CharField(max_length=20, blank=True)


class MultifieldModel(models.Model):
    slug = models.SlugField(max_length=20, unique=True)
    text = models.CharField(max_length=20)


class AbstractBase(models.Model):
    foo = models.CharField(max_length=20)

    class Meta:
        abstract = True


class ConcreteSon(AbstractBase):
    pass


class AbstractSon(AbstractBase):
    class Meta:
        abstract = True


class ConcreteGrandSon(AbstractSon):
    pass


class StandardSon(StandardModel):
    pass


class PointedModel(models.Model):
    foo = models.CharField(max_length=20)


class PointerModel(models.Model):
    bar = models.CharField(max_length=20)
    pointed = models.OneToOneField(
        PointedModel,
        related_name='pointer',
        null=True,
        on_delete=models.CASCADE
    )


class WithDefaultValue(models.Model):
    foo = models.CharField(max_length=20, default='')


WITHFILE_UPLOAD_TO = 'django'
WITHFILE_UPLOAD_DIR = os.path.join(settings.MEDIA_ROOT, WITHFILE_UPLOAD_TO)

class WithFile(models.Model):
    afile = models.FileField(upload_to=WITHFILE_UPLOAD_TO)


if Image is not None:  # PIL is available

    class WithImage(models.Model):
        animage = models.ImageField(upload_to=WITHFILE_UPLOAD_TO)
        size = models.IntegerField(default=0)

else:
    class WithImage(models.Model):
        pass


class WithSignals(models.Model):
    foo = models.CharField(max_length=20)


class CustomManager(models.Manager):

    def create(self, arg=None, **kwargs):
        return super(CustomManager, self).create(**kwargs)


class WithCustomManager(models.Model):

    foo = models.CharField(max_length=20)

    objects = CustomManager()


class AbstractWithCustomManager(models.Model):
    custom_objects = CustomManager()

    class Meta:
        abstract = True


class FromAbstractWithCustomManager(AbstractWithCustomManager):
    pass


# For auto_fields
# ===============


class ComprehensiveMultiFieldModel(models.Model):
    # Text
    chars = models.CharField(max_length=4)  # Below FuzzyText' boundary
    text = models.TextField()
    slug = models.SlugField()

    # Misc
    binary = models.BinaryField()
    boolean = models.BooleanField(default=False)
    nullboolean = models.NullBooleanField()
    if django.VERSION[:2] >= (1, 8):
        uu = models.UUIDField()

    # Date and time
    dt = models.DateField()
    ts = models.DateTimeField()
    time = models.TimeField()
    if django.VERSION[:2] >= (1, 8):
        duration = models.DurationField()

    # Numbers
    nb = models.IntegerField()
    dec = models.DecimalField(max_digits=10, decimal_places=4)
    bigint = models.BigIntegerField()
    posint = models.PositiveIntegerField()
    smallint = models.SmallIntegerField()
    smallposint = models.PositiveSmallIntegerField()
    fl = models.FloatField()

    # Filed
    attached = models.FileField()
    img = models.ImageField()

    # Internet
    ipv4 = models.GenericIPAddressField(protocol='ipv4')
    ipv6 = models.GenericIPAddressField(protocol='ipv6')
    ipany = models.GenericIPAddressField()
    email = models.EmailField()
    url = models.URLField()


class OptionalModel(models.Model):
    req = models.CharField(max_length=10)
    opt = models.CharField(max_length=3, blank=True)


class ForeignKeyModel(models.Model):
    name = models.CharField(max_length=20)
    target = models.ForeignKey(ComprehensiveMultiFieldModel)


class OneToOneModel(models.Model):
    name = models.CharField(max_length=20)
    relates_to = models.OneToOneField(ForeignKeyModel)


class ManyToManySourceModel(models.Model):
    name = models.CharField(max_length=20)
    targets = models.ManyToManyField(ComprehensiveMultiFieldModel)


class ManyToManyThroughModel(models.Model):
    name = models.CharField(max_length=20)
    multi = models.ForeignKey(ComprehensiveMultiFieldModel)
    source = models.ForeignKey('ManyToManyWithThroughSourceModel')


class ManyToManyWithThroughSourceModel(models.Model):
    name = models.CharField(max_length=20)
    targets = models.ManyToManyField(ComprehensiveMultiFieldModel, through=ManyToManyThroughModel)


class CycleAModel(models.Model):
    a_name = models.CharField(max_length=10)
    c_fkey = models.ForeignKey('CycleCModel', null=True)


class CycleBModel(models.Model):
    b_name = models.CharField(max_length=10)
    a_fkey = models.ForeignKey(CycleAModel)


class CycleCModel(models.Model):
    c_name = models.CharField(max_length=10)
    b_fkey = models.ForeignKey(CycleBModel)


