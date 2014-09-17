omero processing scripts
========================

Package for processing of images in omero.

[OMERO](https://www.openmicroscopy.org/site/products/omero) is a platform
for visualizing, managing, and annotating scientific image data.  One of
its components is the OMERO.processor which can launch python scripts for
the processing of images and distribute the load over multiple processor
nodes via OMERO.grid.  This makes it ideal for batch processing of images,
specially computationally-heavy steps.

The `omero.scripts.processing` package aims at creating a common interface
for image processing scripts in omero.

The package defines two base classes, processing `block` and `chain`.  The
first is meant for individual processing steps, e.g., denoising with a
specific algorithm, channel alignment, SI reconstruction, while the later
is sequences of processing blocks.

This design of `block` is based on the idea that the processing of an image
has the following stages:

1. *get image*: in most cases, this is simply downloading the image from
   the omero database.  More complex cases can be multiple input images,
   conversion of the image into a different format.
2. *parse options*: each block may have multiple arguments which should
    be validated.  This is performed after obtaining the image in case
    the default is image dependent.  Still, in many cases it will be
    possible to obtain such default values from the Omero image and not
    from the file.
3. *process*: do the actual image processing.  This step is the main change
    between the multiple subclasses of `block`, and is often split into
    further substeps.
4. *import image*: simply importing the image back into OMERO.  At the
    simplest level, this will be a single image file
5. *annotate*: it is useful to annotate the processed images.  The base
    class will attach a log file to the processed image, and add links
    between the original and processed (parent and child) images.

Examples
--------

The aim is that a processing script becomes something as simple as:

    import omero.scripts.processing
    import omero.scripts.processing.denoise

    dn = omero.scripts.processing.denoise.ndsafir()

    chain = omero.scripts.processing.chain([dn])
    chain.launch()

Of course, most of the work is in defining the new class, but by
inheriting most of the steps from other classes, it should be much
simpler.  Actual examples that are currently in production at
[Micron Oxford](http://www.micron.ox.ac.uk/), are available
[online](https://github.com/MicronOxford/scripts/tree/master/omero).

To create a chain of blocks should be equally simple:

    import omero.scripts.processing
    import omero.scripts.processing.denoise
    import omero.scripts.processing.deconvolution
    import omero.scripts.processing.frobnicate

    dn    = omero.scripts.denoise.ndsafir()
    decon = omero.scripts.deconvolution.decon3d()
    frob  = omero.scripts.frobnicate.frobnicate()

    chain = omero.scripts.processing.chain([dn, decon, frob])
    chain.launch()

Issues and Wishlist
-------------------

* it seems that we can't group related options together without
having a parent and a parent requires some sort of an option.
See https://github.com/openmicroscopy/openmicroscopy/issues/2463

* handle version and contact values when creating the script interface.

* we upload and download the image from omero when going through a chain.
This is safer but we could have an option where the intermediary files
are used which saves us from downloading. Also, maybe some users are not
interested in keeping the intermediary processing steps.

* should probably rename the block subclasses

* arguments on the GUI must have unique names which will not always be true.
For example multiple blocks may have a correction option.

* we have checkboxes for each block in a chain but that was forced on us
so that subgroups work properly.  However, it may actually be a nice feature
but we are not yet checking their values.

* have the chain handling the keep alive

* investigate having the chain getting the image and putting back into
omero, possibly accepting functions from the block. Think chain working
like pipes in a command line and blocks as filters.
