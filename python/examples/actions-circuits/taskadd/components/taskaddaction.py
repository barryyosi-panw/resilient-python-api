#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Resilient Systems, Inc. ("Resilient") is willing to license software
# or access to software to the company or entity that will be using or
# accessing the software and documentation and that you represent as
# an employee or authorized agent ("you" or "your") only on the condition
# that you accept all of the terms of this license agreement.
#
# The software and documentation within Resilient's Development Kit are
# copyrighted by and contain confidential information of Resilient. By
# accessing and/or using this software and documentation, you agree that
# while you may make derivative works of them, you:
#
# 1)  will not use the software and documentation or any derivative
#     works for anything but your internal business purposes in
#     conjunction your licensed used of Resilient's software, nor
# 2)  provide or disclose the software and documentation or any
#     derivative works to any third party.
#
# THIS SOFTWARE AND DOCUMENTATION IS PROVIDED "AS IS" AND ANY EXPRESS
# OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL RESILIENT BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
# OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Action to add a task through a manual action with
3 fields defined for the manual action
"""

import logging
import traceback
from circuits.core.handlers import handler
from resilient_circuits.actions_component import ResilientComponent

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)
logging.Formatter('%(asctime)s:%(name)s:%(levelname)-8s %(message)s')

CONFIG_DATA_SECTION = 'task_add'
CONFIG_SOURCE_SECTION = 'resilient'


class AddTaskAction(ResilientComponent):
    """
    Manual action to add an incident task to a phase
    """

    def __init__(self, opts):
        super(AddTaskAction, self).__init__(opts)
        self.options = opts.get(CONFIG_DATA_SECTION, {})

        # The queue name can be specified in the config file or use default
        self.channel = "actions." + self.options.get("queue", "add_task")

    @handler("add_task")
    def add_task_action(self, event, *args, **kwargs):
        """
        The string passed to @handler must match the action name in Resilient
        """
        log.info("Starting add_task_function")
        log.debug("ARGS: " + repr(args))
        log.debug("KWARGS: " + repr(kwargs))

        try:
            message = event.message
            incident = message['incident']
            incident_id = message['incident']['id']

            # Get the action-field values.  Note that task_phase is an ID.
            tname = message["properties"].get('task_name')
            task_instructions = message["properties"].get('task_instructions')
            task_phase_key = message["properties"].get('task_phase')

            # Resolve the label of the phase, from its ID,
            # using the type-information supplied with the action message
            task_phase = message["type_info"].get(
                'actioninvocation', {}).get('fields', {}).get(
                    'task_phase', {}).get('values', {}).get(
                        str(task_phase_key), {}).get('label', None)

            # We want to assign the task to the user who added it.
            # Check that the user is a member of the incident.
            # - either directly, as owner or in the members list
            # - or indirectly, as a member of a group.
            # (otherwise, adding or assigning the task will fail).

            # Find the person who initiated the action
            user_id = message["user"]["id"]

            # User info includes a list of groups that user belongs to
            client = self.rest_client()
            user_info = client.get("/users/{}".format(user_id))
            user_groups = set(user_info["group_ids"])

            if incident["owner_id"] == user_id:
                # OK, user is the incident owner
                pass
            elif incident["owner_id"] in user_groups:
                # OK, user is a member of the incident owner group
                pass
            elif user_id in incident["members"]:
                # OK, user is a member of the incident
                pass
            elif user_groups & set(incident["members"]):
                # OK, user is in a group that is a member of the incident
                pass
            else:
                # Update the incident membership, adding this user
                def update_func(memberlist):
                    """Callback for get/put, modifies the member list"""
                    memberlist["members"].append(user_id)
                    return memberlist

                log.info("Adding user to member list")
                members_uri = "/incidents/{}/members".format(incident_id)
                client.get_put(members_uri, update_func,
                               co3_context_token=event.context)

            # Create the task
            task = {"name": tname or '(unnamed)',
                    "instr_text": task_instructions or '',
                    "inc_id": incident_id,
                    "phase_id": task_phase or None,
                    "owner_id": user_id}
            log.debug("Submitting task %s", repr(task))

            posted_task = self.rest_client().post("/incidents/%s/tasks" %
                                                  incident_id, task)
            if not posted_task:
                raise Exception("Failed to post task")
            else:
                log.info("Task Posted: %s", posted_task)
                yield "action complete. task posted. ID %s" % posted_task["id"]

        except Exception as e:
            # Reraise exception for handling by framework
            log.error(traceback.format_exc())
            raise
