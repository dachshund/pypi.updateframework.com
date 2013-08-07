#!/usr/bin/env python





"""A simple library to make it easier to delegate targets from delegator A to
delegatee B. This is useful for automation of the development
environment on pypi.updateframework.com."""


import datetime
import os
import time

from tuf.log import logger

import tuf.formats as formats
import tuf.repo.keystore as keystore
import tuf.repo.signercli as signercli
import tuf.repo.signerlib as signerlib

import check





########################### GLOBAL VARIABLES ##################################

KEY_SIZE=2048

TARGETS_ROLE_NAME = "targets"
CLAIMED_TARGETS_ROLE_NAME = "targets/claimed"
RECENTLY_CLAIMED_TARGETS_ROLE_NAME = "targets/recently-claimed"
UNCLAIMED_TARGETS_ROLE_NAME = "targets/unclaimed"

# Map full role names (str) to list of passwords ([str, ..., str]).
# Storing passwords in the clear is generally a bad idea. It is acceptable for
# development (but certainly not production).
ROLE_NAME_TO_PASSWORDS = {
  TARGETS_ROLE_NAME: ['targets'],
  CLAIMED_TARGETS_ROLE_NAME: ['claimed'],
  RECENTLY_CLAIMED_TARGETS_ROLE_NAME: ['recently-claimed'],
  UNCLAIMED_TARGETS_ROLE_NAME: ['unclaimed']
}

KEYSTORE_DIRECTORY = os.path.abspath("keystore")
REPOSITORY_DIRECTORY = os.path.abspath("repository")
# Assume that metadata is in /path/to/repository/metadata
METADATA_DIRECTORY = os.path.join(REPOSITORY_DIRECTORY, "metadata")
# Assume that metadata is in /path/to/repository/targets
TARGETS_DIRECTORY = os.path.join(REPOSITORY_DIRECTORY, "targets")





def check_sanity():
  """Check that we correctly set some parameters."""

  assert os.path.isdir(KEYSTORE_DIRECTORY)
  assert os.path.isdir(REPOSITORY_DIRECTORY)
  assert METADATA_DIRECTORY.startswith(REPOSITORY_DIRECTORY)
  assert os.path.isdir(METADATA_DIRECTORY)
  assert TARGETS_DIRECTORY.startswith(REPOSITORY_DIRECTORY)
  assert os.path.isdir(TARGETS_DIRECTORY)





def generate_rsa_keys(passwords):
  """Return the IDs of RSA keys encrypted in order with the given passwords."""

  rsa_keys = []

  for password in passwords:
    # Generate the RSA key and save it to 'keystore_directory'.
    rsa_key = \
      signerlib.generate_and_save_rsa_key(keystore_directory=KEYSTORE_DIRECTORY,
                                          password=password, bits=KEY_SIZE)
    rsa_key_id = rsa_key['keyid']
    rsa_keys.append(rsa_key_id)

  return rsa_keys





def get_absolute_delegated_paths(files_directory, recursive_walk=True,
                                 followlinks=True,
                                 file_predicate=signerlib.accept_any_file):
  """Given the directory which contains the target files of interest to the
  delegatee (files_directory), walk the directory recursively (or not: see
  recursive_walk) and follow symbolic links (or not: see followlinks) to
  determine and return all target files satisfying a predicate (any file is
  accepted by default: see file_predicate)."""

  # Retrieve for the targets role targets matching the given file predicate.
  absolute_delegated_paths = \
    signerlib.get_targets(files_directory, recursive_walk=recursive_walk,
                          followlinks=followlinks,
                          file_predicate=file_predicate)

  return absolute_delegated_paths





def get_keys_for_targets_role(targets_role_name):
  """Given the name of a targets role (targets_role_name) and its list of
  passwords (targets_role_passwords), return the RSA key IDs for a targets role.

  Side effect: Load the aforementioned RSA keys into the global TUF
  keystore."""

  # Get the list of passwords for this role.
  targets_role_passwords = ROLE_NAME_TO_PASSWORDS[targets_role_name]

  # Get all the target roles and their respective keyids.
  # These keyids will let the user know which roles are currently known.
  # signerlib.get_target_keyids() returns a dictionary that looks something
  # like this: {'targets': [keyid1, ...], 'targets/role1': [keyid1, ...] ...}
  targets_roles = signerlib.get_target_keyids(METADATA_DIRECTORY)

  # Either get extant keys or create new ones for the targets role.
  # NOTE: Yes, suppose we have generated these keys before but never associated
  # them with the targets role. Then we would unnecessarily create new keys.
  # One solution is a tool that finds unassociated keys and offers to delete
  # them. 
  if targets_role_name in targets_roles:
    targets_role_keys = targets_roles[targets_role_name]
  else:
    targets_role_keys = generate_rsa_keys(targets_role_passwords)

  # Decrypt and load the keys of the targets role.
  loaded_targets_role_keys = \
    keystore.load_keystore_from_keyfiles(KEYSTORE_DIRECTORY, targets_role_keys,
                                         targets_role_passwords)
  assert targets_role_keys == loaded_targets_role_keys

  return targets_role_keys





def get_relative_delegated_paths(absolute_delegated_paths):
  relative_delegated_paths = []

  # Map absolute delegated paths to be relative to the TARGETS_DIRECTORY.
  # This means that they must start with the relative "targets/" path.
  for absolute_delegated_path in absolute_delegated_paths:
    assert absolute_delegated_path.startswith(TARGETS_DIRECTORY+'/')
    relative_delegated_path = \
      absolute_delegated_path[len(REPOSITORY_DIRECTORY)+1:]
    relative_delegated_paths.append(relative_delegated_path)

  return relative_delegated_paths





def make_delegation(delegator_targets_role_name, delegatee_targets_role_name,
                    relative_delegated_paths=None,
                    path_hash_prefix=None):

  # The name of the delegatee contain the name of its delegator as a prefix.
  assert \
    delegatee_targets_role_name.startswith(delegator_targets_role_name+'/')

  # relative_delegated_paths XOR path_hash_prefix
  assert (relative_delegated_paths is None and path_hash_prefix is not None) \
          or \
         (relative_delegated_paths is not None and path_hash_prefix is None)

  # Load targets roles keys into memory, and get their key IDs.
  delegator_targets_role_keys = \
    get_keys_for_targets_role(delegator_targets_role_name)
  delegatee_targets_role_keys = \
    get_keys_for_targets_role(delegatee_targets_role_name)

  # We need to extract the relative name of the full delegatee targets role
  # name. For example, if the delegator is 'targets/a' and its delegatee is
  # 'targets/a/b', then the relative name of the delegatee is 'b'.
  relative_delegatee_targets_role_name = \
    delegatee_targets_role_name[len(delegator_targets_role_name)+1:]

  # Update the delegator metadata file.
  update_delegator_metadata(delegator_targets_role_name,
                            relative_delegatee_targets_role_name,
                            delegator_targets_role_keys,
                            delegatee_targets_role_keys,
                            relative_delegated_paths=relative_delegated_paths,
                            path_hash_prefix=path_hash_prefix)





def need_delegation(targets_role_name, files_directory, recursive_walk=True,
                    followlinks=True,
                    file_predicate=signerlib.accept_any_file):
  """Given a targets role name (targets_role_name) and the directory which
  contains the target files of interest (files_directory), walk the directory
  recursively (or not: see recursive_walk) and follow symbolic links (or not:
  see followlinks) to see whether any target file satisfying a predicate (any
  file is accepted by default: see file_predicate) needs to be added, updated
  or deleted."""

  matched = False

  try:
    matched = check.metadata_matches_data(METADATA_DIRECTORY,
                                          TARGETS_DIRECTORY,
                                          targets_role_name, files_directory,
                                          recursive_walk=recursive_walk,
                                          followlinks=followlinks,
                                          file_predicate=file_predicate)
  except check.MissingTargetMetadataError:
    # The metadata for this targets role is missing, so it certainly needs
    # delegation!
    matched = False

  return not matched





# TODO: Update delegator only if necessary to do so.
def update_delegator_metadata(delegator_targets_role_name,
                              relative_delegatee_targets_role_name,
                              delegator_targets_role_keys,
                              delegatee_targets_role_keys,
                              relative_delegated_paths=None,
                              path_hash_prefix=None):

  # relative_delegated_paths XOR path_hash_prefix
  assert (relative_delegated_paths is None and path_hash_prefix is not None) \
          or \
         (relative_delegated_paths is not None and path_hash_prefix is None)

  signercli._update_parent_metadata(METADATA_DIRECTORY,
                            relative_delegatee_targets_role_name,
                            delegatee_targets_role_keys,
                            delegator_targets_role_name,
                            delegator_targets_role_keys,
                            delegated_paths=relative_delegated_paths,
                            path_hash_prefix=path_hash_prefix)





# TODO: Update delegatee only if necessary to do so.
def update_targets_metadata(targets_role_name, relative_delegated_paths,
                            targets_role_keys, time_delta):

  # TODO: Ensure that targets_role_name is of the correct form.
  assert targets_role_name == 'targets' or \
         targets_role_name.startswith('targets/')

  # The first time a parent role creates a delegation, a directory
  # containing the parent role's name is created in the metadata
  # directory.  For example, if the targets roles creates a delegated
  # role 'role1', the metadata directory would then contain:
  # '{METADATA_DIRECTORY}/targets/role1.txt', where 'role1.txt' is the
  # delegated role's metadata file.
  # If delegated role 'role1' creates its own delegated role 'role2', the
  # metadata directory would then contain:
  # '{METADATA_DIRECTORY}/targets/role1/role2.txt'.
  # When creating a delegated role, if the parent directory already
  # exists, this means a prior delegation has been perform by the parent.

  parent_role_name = os.path.dirname(targets_role_name)
  child_role_name = os.path.basename(targets_role_name)

  parent_role_directory = os.path.join(METADATA_DIRECTORY, parent_role_name)
  if not os.path.isdir(parent_role_directory):
    os.mkdir(parent_role_directory)

  # Set the filename of the targets role metadata.
  targets_role_filename = child_role_name+'.txt'
  targets_role_filename = os.path.join(parent_role_directory,
                                       targets_role_filename)

  # TODO: Increment version number on update by reading a verified copy of
  # extant metadata.
  version_number = 1
  # TODO: Adjust for UTC.
  # http://stackoverflow.com/a/2775982
  future_date = datetime.datetime.now() + time_delta
  expiration_date = formats.format_time(time.mktime(future_date.timetuple()))

  # Prepare the targets metadata.
  targets_metadata = \
    signerlib.generate_targets_metadata(REPOSITORY_DIRECTORY,
                                        relative_delegated_paths,
                                        version_number, expiration_date)

  # Sign and write the targets role metadata.
  signercli._sign_and_write_metadata(targets_metadata, targets_role_keys,
                                     targets_role_filename)





########################### GLOBAL STATEMENTS #################################

# Perform sanity checks when this module is imported.
check_sanity()




