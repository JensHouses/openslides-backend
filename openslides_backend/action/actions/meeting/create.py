from typing import Any, Dict, Iterable, List, Type, cast

from ....models.models import Meeting
from ....shared.exceptions import ActionException
from ....shared.patterns import Collection, FullQualifiedId
from ...action import Action
from ...mixins.create_action_with_dependencies import CreateActionWithDependencies
from ...util.default_schema import DefaultSchema
from ...util.register import register_action
from ..group.create import GroupCreate
from ..motion_workflow.create import MotionWorkflowCreateSimpleWorkflowAction
from ..projector.create import ProjectorCreateAction
from ..projector_countdown.create import ProjectorCountdownCreate
from ..user.update import UserUpdate
from .shared_meeting import meeting_projector_default_replacements


@register_action("meeting.create")
class MeetingCreate(CreateActionWithDependencies):
    model = Meeting()
    schema = DefaultSchema(Meeting()).get_create_schema(
        required_properties=["committee_id", "name", "welcome_title"],
        optional_properties=[
            "welcome_text",
            "description",
            "location",
            "start_time",
            "end_time",
            "url_name",
            "enable_anonymous",
            "guest_ids",
        ],
    )
    dependencies = [
        MotionWorkflowCreateSimpleWorkflowAction,
        ProjectorCreateAction,
    ]

    def update_instance(self, instance: Dict[str, Any]) -> Dict[str, Any]:
        instance = super().update_instance(instance)

        action_data = [
            {
                "name": "Default",
                "meeting_id": instance["id"],
                "permissions": [
                    "agenda_item.can_see_internal",
                    "assignment.can_see",
                    "list_of_speakers.can_see",
                    "mediafile.can_see",
                    "meeting.can_see_frontpage",
                    "motion.can_see",
                    "projector.can_see",
                    "user.can_see",
                    "user.can_change_own_password",
                ],
            },
            {
                "name": "Admin",
                "meeting_id": instance["id"],
            },
            {
                "name": "Delegates",
                "meeting_id": instance["id"],
                "permissions": [
                    "agenda_item.can_see_internal",
                    "assignment.can_nominate_other",
                    "assignment.can_nominate_self",
                    "list_of_speakers.can_be_speaker",
                    "mediafile.can_see",
                    "meeting.can_see_autopilot",
                    "meeting.can_see_frontpage",
                    "motion.can_create",
                    "motion.can_create_amendments",
                    "motion.can_support",
                    "projector.can_see",
                    "user.can_see",
                    "user.can_change_own_password",
                ],
            },
            {
                "name": "Staff",
                "meeting_id": instance["id"],
                "permissions": [
                    "agenda_item.can_manage",
                    "assignment.can_manage",
                    "assignment.can_nominate_self",
                    "list_of_speakers.can_be_speaker",
                    "list_of_speakers.can_manage",
                    "mediafile.can_manage",
                    "meeting.can_see_frontpage",
                    "meeting.can_see_history",
                    "motion.can_manage",
                    "projector.can_manage",
                    "tag.can_manage",
                    "user.can_manage",
                    "user.can_change_own_password",
                ],
            },
            {
                "name": "Committees",
                "meeting_id": instance["id"],
                "permissions": [
                    "agenda_item.can_see_internal",
                    "assignment.can_see",
                    "list_of_speakers.can_see",
                    "mediafile.can_see",
                    "meeting.can_see_frontpage",
                    "motion.can_create",
                    "motion.can_create_amendments",
                    "motion.can_support",
                    "projector.can_see",
                    "user.can_see",
                ],
            },
        ]
        action_results = self.execute_other_action(GroupCreate, action_data)

        fqid_default_group = FullQualifiedId(
            Collection("group"), action_results[0]["id"]  # type: ignore
        )
        fqid_admin_group = FullQualifiedId(Collection("group"), action_results[1]["id"])  # type: ignore
        assert (
            self.datastore.additional_relation_models[fqid_default_group]["name"]
            == "Default"
        )
        assert (
            self.datastore.additional_relation_models[fqid_admin_group]["name"]
            == "Admin"
        )
        instance["default_group_id"] = fqid_default_group.id
        instance["admin_group_id"] = fqid_admin_group.id

        # Add user to admin group
        action_data = [
            {
                "id": self.user_id,
                "group_$_ids": {str(instance["id"]): [fqid_admin_group.id]},
            }
        ]
        self.execute_other_action(UserUpdate, action_data)
        self.apply_instance(instance)
        action_data_countdowns = [
            {
                "title": "List of speakers countdown",
                "meeting_id": instance["id"],
            },
            {
                "title": "Voting countdown",
                "meeting_id": instance["id"],
            },
        ]
        action_results = self.execute_other_action(
            ProjectorCountdownCreate,
            action_data_countdowns,
        )
        instance["list_of_speakers_countdown_id"] = action_results[0]["id"]  # type: ignore
        instance["poll_countdown_id"] = action_results[1]["id"]  # type: ignore

        return instance

    def get_dependent_action_data(
        self, instance: Dict[str, Any], CreateActionClass: Type[Action]
    ) -> List[Dict[str, Any]]:
        if CreateActionClass == MotionWorkflowCreateSimpleWorkflowAction:
            return [
                {
                    "name": "Simple Workflow",
                    "default_workflow_meeting_id": instance["id"],
                    "default_amendment_workflow_meeting_id": instance["id"],
                    "default_statute_amendment_workflow_meeting_id": instance["id"],
                    "meeting_id": instance["id"],
                }
            ]
        elif CreateActionClass == ProjectorCreateAction:
            return [
                {
                    "name": "Default projector",
                    "meeting_id": instance["id"],
                    "used_as_reference_projector_meeting_id": instance["id"],
                    "used_as_default_$_in_meeting_id": {
                        name: instance["id"]
                        for name in meeting_projector_default_replacements
                    },
                }
            ]
        return []

    def validate_fields(self, instance: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check for guest_ids being in committee/member_ids or committee/manager_ids
        """
        instance = super().validate_fields(instance)
        if instance.get("guest_ids"):
            committee = self.datastore.get(
                FullQualifiedId(Collection("committee"), instance["committee_id"]),
                ["member_ids", "manager_ids"],
            )
            diff = (
                set(cast(Iterable[Any], instance.get("guest_ids")))
                - set(cast(Iterable[Any], committee.get("member_ids", ())))
                - set(cast(Iterable[Any], committee.get("manager_ids", ())))
            )
            if diff:
                raise ActionException(
                    f"Guest-ids {diff} are not part of committee-member or manager_ids."
                )
        return instance