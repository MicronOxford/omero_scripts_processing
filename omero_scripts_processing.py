#!/usr/bin/env python
# -*- coding: utf-8 -*-

## Copyright (C) 2014 David Pinto <david.pinto@bioch.ox.ac.uk>
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU Affero General Public License as published by
## the Free Software Foundation; either version 3 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
## GNU Affero General Public License for more details.
##
## You should have received a copy of the GNU Affero General Public License
## along with this program; if not, see <http://www.gnu.org/licenses/>.

import os
import os.path
import subprocess
import fcntl
import time
import tempfile
import sys
import distutils.spawn
import time

import omero.scripts
import omero.gateway
import omero.cli
import omero.rtypes

class processing_error(Exception):
  """Base exception class for omero_scripts_processing."""

class chain_error(processing_error):
  """Error from the chain."""

class block_error(processing_error):
  """Error from a processing block."""

class no_bin(block_error):
  """No executable binary found on path."""

class invalid_parameter(block_error):
  """One of the processing parameters is invalid."""

class invalid_image(block_error):
  """Image cannot be processed for some reason."""

class timeout_reached(block_error):
  """Processing took to long and reached timeout."""

class bin_bad_exit(block_error):
  """Binary in bin block exited with non-zero."""


class block(object):
  """Base class for individual image processing blocks.

  These are meant to be used as building blocks of processing chains.
  """

  title = ""
  """Title of the processing block."""

  doc = ""
  """String literal.  This text is displayed when calling the script in
  omero.  Can be set to the class docstring.
  """

  version      = ""   # string with version number of the script
  authors      = []   # list of strings with script authors
  institutions = []   # list of strings with institution names
  contact      = ""   # string with contact name

  def __init__(self):
    """Construct an omero scripts processing block.

    Attributes initilized:
      args: list of options for the block, an array of omero.scripts
        data types.
    """

    ## Why are the arguments instance rather than class attributes?
    ##  1) to allows different defaults in a processing chain where the
    ##     same block appears repeated.
    ##  2) as part of creating the GUI, we will modify some of their
    ##     attributes (grouping and name), which can leads to weird bugs
    ##     when the same block appears repeated in the chain.
    self.args = []
    self._tmpfiles = []

  ## TODO investigate something nicer
  def get_tmp_file(self, suffix = ""):
    """Create temporary file to be removed at the end of processing.
    """
    f = tempfile.NamedTemporaryFile(suffix = suffix)
    self._tmpfiles.append(f)
    return f

  ## TODO investigate something nicer
  def clean_tmp_files(self):
    """Remove all temporary files created by this instance."""
    self._tmpfiles = []

  def launch(self, parent):
    """Performs the whole processing block."""
    try:
      self.get_parent(parent)
      self.parse_options()
      self.process()
      self.send_child()
      self.annotate()
    finally:
      self.clean_tmp_files()

  def get_parent(self, parent):
    """Get parent image.

    Args:
      parent: omero.gateway._ImageWrapper of the image to be processed.

    Responsabilities:
      * set `parent` attribute, the omero.gateway._ImageWrapper
        object of the parent image.
      * set `datasetID` attribute, the dataset to where the
        processed image should be placed.
      * set `fin` attribute, typically a `file` object of a temporary
        file in the filesystem, but can also be a numpy array (for the
        python_block subclass).
    """
    self.parent = parent

    ## TODO it seems that it is possible for an image to be in multiple
    ##      datasets but we haven't come across it.  At the moment this
    ##      will pick the first listed, we can think of something to do
    ##      when it actually becomes a problem.
    self.dataset = None
    for p in parent.listParents():
      self.datasetID = p.getId()
      break

  def parse_options(self):
    """Create list of arguments that is used.

    Responsabilities:
      * set `options` attribute with a `dict` of option names and their
        values.
    """
    pass

  def process(self):
    raise NotImplementedError()

  def send_child(self):
    raise NotImplementedError()

  def annotate(self):
    """Annotate parent and child about the processing.

    1) Connect two images with parent-child relationship.

      Omero does not yet have a concept of parent child relationship.
      The best we can do for now is to leave a note on the parent and
      child description pointing to each other.  This notes are of
      the style "parent of image ID: #" which on omero.web and
      omero.insight create a link to the other image.  This link
      syntax was broken for Omero versions 5.0.1 and 5.0.2.

    2) Attach log file to the child image.

    Sub-classes are recommended to perform their own annotation and
    then call this method.

    Args:
      flog: `file` object for a log file. Such file will be attached
        to the child image.
    """
    def append_to_description(img, relationship, to):
      desc = "\n".join([
        img.getDescription(),
        "%s Image ID: %i" % (relationship, to.getId())
      ])
      img.setDescription(desc)
      img.save()
    append_to_description(self.parent, "parent of", self.child)
    append_to_description(self.child, "child of", self.parent)


class bin_block(block):
  """Processing block for binaries.

  This is the superclass to use when the processing step is done
  by a typical application installed in the system, something that
  can accept typical command line options, acts on an image file
  that is on the filesystem, and creates a file with the processed
  image.
  """

  def __init__(self, bin_path = None):
    """Constructor.

    Args:
      bin_path: string defining the path for the binary to use.  If None,
        default to the class name to find an executable in the system.

    Responsabilities:
      * set the `bin` attribute with the path for an executable.
    """
    super(bin_block, self).__init__()

    self.bin = (bin_path
                or distutils.spawn.find_executable(self.__class__.__name__))
    if not self.bin:
      raise no_bin("No executable path defined")
    elif not os.path.exists(self.bin):
      raise no_bin("Executable `%s` does not exist" % self.bin)
    elif not os.path.isfile(self.bin):
      raise no_bin("Path `%s` is not an executable" % self.bin)

  def get_parent(self, parent):
    ## TODO We can have this method use self.parent.exportOmeTiff() by
    ##      default when the subclass does not get a file.
    super(bin_block, self).get_parent(parent)

  def parse_options(self):
    """Create list of arguments that is used.

    Responsabilities:
      * set `options` attribute with a `dict` of option names and their
        values.
    """
    super(bin_block, self).parse_options()

  def process(self, args, stderr = None, stdout = None,
              timeout = None, timeout_grain = 10):
    """
    A subclass can also set the timeout based on characteristics of
    the image being processed.

    Args:
      args: list of strings to be used when creating the process.
      stderr: file where to redirect the process stderr.
      stdout: file where to redirect the process stdout.
      timeout: time in seconds before timing out the process in which
        case an exception is raised.  Set to None, for no timeout.
      timeout_grain: time interval in seconds when the process status
        is being checked.

    Raises:
      timeout_reached: timeout was reached before processing ended.
      bin_bad_exit: process exited with a non-zero status.
    """

    if timeout:
      timeout = lambda : time.time() > timeout
    else:
      timeout = lambda : False

    self.flog.write("$ %s\n" % " ".join(args))
    self.flog.flush()

    p = subprocess.Popen(args, stderr = stderr, stdout = stdout)
    def finished():
      return p.poll() is not None
    while not finished() and not timeout():
      self.conn.keepAlive()
      time.sleep(timeout_grain)

    if finished() and p.returncode != 0:
      raise bin_bad_exit("`%s` exited with status %i", args, p.returncode)
    elif timeout():
      p.terminate()
      raise timeout_reached("processing exceedeed timeout")

  def send_child(self):
    """Send/export/upload processed image back into omero."""

    cli = omero.cli.CLI()
    cli.loadplugins()

    ## TODO replace with a property setter once it is implemented
    ##      https://trac.openmicroscopy.org.uk/ome/ticket/12388
    cli._client = self.client.createClient(secure = True)

    cmd = [
      "import",
      "--debug", "ERROR",
    ]
    if self.datasetID:
      cmd.extend(["-d", str(self.datasetID)])
    if self.child_name:
      cmd.extend(["-n", self.child_name])

    ## TODO experiment setting STDOUT into a variable rather than temporary
    ##      file such with StringIO

    ## The ID of exported image will be printed back to STDOUT. So we need
    ## to catch it in file, and read that file to get its ID. And yeah, this
    ## is a bit convoluted but it is the recommended method.
    cid = None
    with tempfile.NamedTemporaryFile(suffix=".stdout") as stdout:
      ## FIXME when stuff is printed to stderr, the user will get a file
      ##       to download with that text. Unfortunately, non-errors are
      ##       still being printed there. The filtering is broken in 5.0.1
      ##       but on future releases we may be able to simply not set
      ##       "---errs" option.
      ##       https://github.com/openmicroscopy/openmicroscopy/issues/2477
      cmd.extend([
        "---errs", os.devnull,
        "---file", stdout.name,
      ])
      cmd.append(self.fout.name)

      ## FIXME https://github.com/openmicroscopy/openmicroscopy/issues/2476
      STDERR = sys.stderr
      try:
        with open(os.devnull, 'w') as DEVNULL:
          sys.stderr = DEVNULL
          cli.invoke(cmd)
      finally:
        sys.stderr = STDERR
      ret_code = cli.rv

      if ret_code == 0:
        ## we only need to read one line or something is very wrong
        cid = int(stdout.readline())
        if not cid:
          raise Exception("unable to get exported image ID")
      else:
        ## I am not going to redirect stderr to a temp file, read it back
        ## in case of an error, and then print it to stderr myself so that
        ## the user gets a file to download with the errors. This is being
        ## fixed upstream already.
        ## https://github.com/openmicroscopy/openmicroscopy/issues/2477
        raise Exception("failed to import processed image into the database")

    self.child = self.conn.getObject("Image", cid)

  def annotate(self):
    super(bin_block, self).annotate()
    self.flog.flush()
    if os.path.getsize(self.flog.name) > 0:
      ## get file extension from the tempfile name to use for rename
      ## after upload.
      ext = os.path.splitext(os.path.split(self.flog.name)[-1])[-1]
      self.child.linkAnnotation(
        self.conn.createFileAnnfromLocalFile(
          self.flog.name,
          origFilePathAndName = self.child_name + ext,
        )
      )


class python_block(block):
  """Processing block for python code.

  This is the superclass to use when the processing is done in
  python and there is no need to actually get a file, i.e., the
  numpy array obtained from omero is enough.
  """

  def __init__(self):
    raise NotImplementedError()


class pipe_block(bin_block):
  """Processing block for interactive applications.

  This is the superclass to use when the processing is done by some
  other application that can only be done interactively requiring
  pipes for the interprocess communication.

  If possible at all, avoid this class by creating non-interactive
  applications or libraries.  At the moment, Matlab code is the only
  where you must do it this way (its '-r' option is not not very
  reliable due to weird behaviour of Mathwork's bash script when
  facing newlines).  Even if using Octave, avoid this class and instead
  write an actual program (or use the `--eval` option.  Either way,
  the session will not persist at end or on error.
  """

  def process(self, img):
    """Do pretty much everything.

    Args:
      img: omero.gateway._ImageWrapper

    Returns:
      The omero.gateway._ImageWrapper of the processed image.
    """
    pass


class matlab_block(pipe_block):
  """A processing block for Matlab "programs".

  Because Matlab is really not meant to do this sort of things.
  """

  interpreter = distutils.spawn.find_executable("matlab")
  """The path for Matlab's intepreter."""

  interpreter_options = ["-nodisplay", "-nosplash", "-nojvm"]
  """List of options to use when starting Matlab."""

  @staticmethod
  def bool_py2m(b):
    """Convert Python boolean values into Matlab."""
    return 'true()' if b else 'false()'

  def get_parent(self, parent):
    """Get parent image into an image file.

    Creates an ome.tiff and sets self.fin to it.
    """
    super(matlab_block, self).get_parent(parent)
    self.fin = self.get_tmp_file(suffix = ".ome.tiff")
    fin = tempfile.NamedTemporaryFile(suffix = ".ome.tiff")
    fin.write(im.exportOmeTiff())
    fin.flush()

  @staticmethod
  def protect_exit(self, code):
    """Enclose the Matlab code in an try/catch block.

    This modifies `code` property so that it is enclosed in a
    try/catch block so that if errors happen in the Matlab code,
    we can still exit

    This is required because the Matlab session persists after an
    error.  This is still not fool-proof.  Syntax errors in the block
    will error the interpreter, and arbitrary commands can be given
    to the session.  Severe input checking is recommended, specially
    for things such as newlines or ' in strings.  Setting a timeout
    is also recommended.
    """

    protected = (
      "omero_scripts_processing_status = 0;\n"
      "try\n"
      "\n"
      "%s\n"
      "\n"
      "catch omero_scripts_processing_err\n"
      "  disp (['error: ' omero_scripts_processing_err.message()]);\n"
      "  disp (omero_scripts_processing_err.stack ());\n"
      "  omero_scripts_processing_status = 1;\n"
      "end\n"
      "exit (omero_scripts_processing_status);\n"
    ) % (code)
    return protected

  def start_matlab(self):
    """Start the Matlab session."""

    self.session = subprocess.Popen(
      self.interpreter + self.interpreter_options,
      stdin  = subprocess.PIPE,
      stdout = subprocess.PIPE,
    )

    ## Sleeping for 5 seconds should be enough to initialize Matlab, and
    ## get its header printed to stdout (which is still printed despite
    ## -nosplash), so we can collect and discard it before starting.
    time.sleep(5)
    try:
      old_flags = fcntl.fcntl(self.session.stdout, fcntl.F_GETFL)
      fcntl.fcntl(self.session.stdout, fcntl.F_SETFL,
                  old_flags | os.O_NONBLOCK)
      self.session.stdout.read() # discard Matlab's splash screen
    finally:
      fcntl.fcntl(self.session.stdout, fcntl.F_SETFL, old_flags)

  def run_matlab(self, timeout = None, timeout_grain = 10)
    """Actually runs the code in Matlab.

    Because of the way Matlab works, it is highly recommended to set
    a Timeout.
    """
    ## FIXME merge this with bin_block process method

    if timeout:
      timeout = lambda : time.time() > timeout
    else:
      timeout = lambda : False

    self.session.stdin.write(self.code)
    self.session.stdin.flush()

    def finished():
      return self.session.poll() is not None
    while not finished() and not timeout():
      self.conn.keepAlive()
      time.sleep(timeout_grain)

    if finished() and p.returncode != 0:
      raise bin_bad_exit("`%s` exited with status %i", args, p.returncode)
    elif timeout():
      p.terminate()
      raise timeout_reached("processing exceedeed timeout")

    ## TODO figure out StringIO to avoid extra file here
    self.flog = self.get_tmp_file(suffix = ".code")
    self.flog.write(self.code)
    self.flog.flush()

  def process(self):
    self.create_code()
    self.start_matlab()
    self.run_matlab()


class chain(object):
  """Processing chain

  Later this will hopefully also allow for having multiple blocks
  and do everything sequential.  In the mean time, it only handles
  one block chains.
  """

  def __init__(self, blocks):
    """
    Args:
        blocks: list of omero_ext.processing.block classes.
    """
    self.blocks = blocks

    ## General selection of objects
    self.args = [
      omero.scripts.String(
        "Data_Type",
        optional    = False,
        default     = "Image",
        values      = ["Dataset", "Image"],
        description = "Choose Images by their IDs or via their 'Dataset'",
        grouping    = "0.1",
      ),
      omero.scripts.List(
        "IDs",
        optional    = False,
        description = "List of Dataset IDs or Image IDs",
        grouping    = "0.2",
      ),
    ]

    nBlocks = len(blocks)
    if not nBlocks:
      raise Exception("no processing blocks")
    elif nBlocks == 1:
      self.title = blocks[0].title
      self.doc   = blocks[0].doc
    else:
       ## TODO What should it be the title and script documentation?
      raise NotImplementedError("multiple blocks not yet implemented")

    ## Set list of arguments
    bg = "%0" + str(len(str(nBlocks))) + "d"
    for n in range(nBlocks):
      block = blocks[n]
      subgroup = bg % (n+1)

      ## XXX: removing the boolean option between a list of options, will
      ## cause https://github.com/openmicroscopy/openmicroscopy/issues/2463

      ## FIXME if we have multiple equal processing blocks, e.g.,
      ##       sequential denoising steps with different settings,
      ##       we are in for a surprise since the name of each option
      ##       must be unique.
      self.args.append(omero.scripts.Bool(
        block.title, default = True, grouping = subgroup,
      ))
      for arg in block.args:
        ## When adding each block list of arguments, we change their
        ## grouping value so that they appear in the correct order.
        arg.grouping = subgroup + "." + arg.grouping
        self.args.append(arg)

  def get_roots(self, data_type, ids):
    """Get all images from IDs, either image or dataset IDs.

    Retrieves all image from a list of IDs. If the IDs are Dataset IDs, then
    returns all images objects from those datasets.

    Args:
        ids: list of integers with the IDs to retrieve.
        data_type: string with the data type that ids corresponds to. It can be
            Image or Dataset.

    Returns:
        List of omero.gateway._ImageWrapper
    """
    objs = self.conn.getObjects(data_type, ids)
    if data_type == "Image":
      imgs = objs
    else:
      ## flatten list from generators
      imgs = [img for ds in objs for img in ds.listChildren()]
    return imgs

  def launch(self):
    """Start the chain of processing blocks.
    """

    self.client = omero.scripts.client(self.title, self.doc, *self.args)
    self.conn = omero.gateway.BlitzGateway(client_obj = self.client)

    ## XXX http://lists.openmicroscopy.org.uk/pipermail/ome-users/2014-September/004775.html
    router = self.client.getProperty("Ice.Default.Router")
    router = self.client.getCommunicator().stringToProxy(router)
    for endpoint in router.ice_getEndpoints():
      host = endpoint.getInfo().host
      self.client.ic.getProperties().setProperty("omero.host", host)
      break
    else:
      ## If it fails for some reason, let's default to 'localhost'
      self.client.ic.getProperties().setProperty("omero.host", "localhost")

    ## TODO when we get multiple blocks this will get tricky
    params = self.client.getInputs(unwrap = True)

    ## Prepare parameters for each block.  We need to filter out
    ## a bunch of stuff. TODO we will probably have to rename them
    ## for display and here we should fix that
    for block in self.blocks:
      ks = [arg.name() for arg in block.args]
      ## beware https://github.com/openmicroscopy/openmicroscopy/issues/2462
      ## we must check if there's really a key with the value since the
      ## script client removes optional values.
      block.options = dict((k, params[k]) for k in ks if k in params.keys())

    nbads = 0
    nimgs = 0
    for root in self.get_roots(params["Data_Type"], params["IDs"]):
      nimgs += 1
      parent = root
      try:
        for block in self.blocks:
          block.conn = self.conn
          block.client = self.client
          child = block.launch(parent)
          parent = child
      except Exception as e:
        ## TODO We are just counting the number of failures
        ##      and success but we need to compile a list of
        ##      problems and give it back to the user at the end
        nbads += 1

    if nimgs == 0:
      msg = "No images selected"
    elif nbads == nimgs:
      msg = "Failed denoising all images"
    elif nbads:
      msg = "Failed denoising %i of %i images" % (nbads, nimgs)
    else:
      msg = "Finished denoising all images"
    self.client.setOutput("Message", omero.rtypes.rstring(msg))

