from contextlib import contextmanager
from dataclasses import dataclass, field

from charter.charts.types import BaseChart
from charter.generators.base import IChartGenerator


class Step:
    pass


@dataclass
class Control(Step):
    is_active: bool


@dataclass
class LoopControl(Control):
    how_many_iterations: str | None = None


@dataclass
class GroupControl(Control):
    text: str | None = None


@dataclass
class CaseControl(Control):
    text: str | None = None


@dataclass
class ConditionControl(Control):
    pass


@dataclass
class ParticipantActivationControl(Control):
    participant: "SequenceDiagramParticipant"


@dataclass
class ForwardStep(Step):
    text: str
    from_participant: "SequenceDiagramParticipant"
    to_participant: "SequenceDiagramParticipant"


@dataclass
class ReturnStep(Step):
    text: str
    from_participant: "SequenceDiagramParticipant"
    to_participant: "SequenceDiagramParticipant"


@dataclass
class SequenceDiagramParticipant:
    sequence_ref: "SequenceDiagram"
    title: str

    def __add_step(self, step: ForwardStep | ReturnStep | ParticipantActivationControl):
        self.sequence_ref._SequenceDiagram__add_step(step)  # noqa

    def go_to(
        self, to: "SequenceDiagramParticipant", text: str = ""
    ) -> "SequenceDiagramParticipant":
        self.__add_step(ForwardStep(text, from_participant=self, to_participant=to))
        return to

    def return_to(
        self, to: "SequenceDiagramParticipant", text: str = ""
    ) -> "SequenceDiagramParticipant":
        self.__add_step(ReturnStep(text, from_participant=self, to_participant=to))
        return to

    @contextmanager
    def activate(self):
        """
        Explicitly activate the participant in the diagram on start of the context manager and ends on its exit.
        Can be used to activate the participant *before* it will participate in any activity
        """
        self.__add_step(ParticipantActivationControl(is_active=True, participant=self))
        yield None
        self.__add_step(ParticipantActivationControl(is_active=False, participant=self))

    def __hash__(self):
        return hash(self.title)

    def __repr__(self):
        return f"Participant ({self.title})"


@dataclass
class SequenceDiagram(BaseChart):
    """
    A sequence of the steps that must be rendered to the diagram DSL - depending on the chosen renderer.

    :title: The title of the diagram to display on the top of the diagram
    :renderer_cls: the class of the renderer to be used to generate the diagram
    :auto_activation: The flag used to track whether the participant should be activated every time it
        has evoked the action to another participant.
        Once the control flow has returned back and the initial active participant was the target of the action, the
        active participant must be deactivated.
        True by default.
    """

    title: str
    generator_cls: type[IChartGenerator]
    auto_activation: bool = True

    __participants: list[SequenceDiagramParticipant] = field(init=False)
    __sequence: list[Step] = field(init=False)
    __auto_activation_stack: list[
        tuple[SequenceDiagramParticipant, SequenceDiagramParticipant]
        | tuple[None, SequenceDiagramParticipant]
    ] = field(init=False)
    __generator: IChartGenerator = field(init=False)
    __inside_condition: bool = field(init=False)

    def __post_init__(self):
        self.__participants = []
        self.__sequence = []
        self.__inside_condition = False
        self.__auto_activation_stack = []
        self.__generator = self.generator_cls(self)

    def participant(self, title: str) -> SequenceDiagramParticipant:
        # NB: every participant must have a unique name
        if title in [_.title for _ in self.__participants]:
            raise AssertionError(
                f"Sequence diagram already contains participant {title}. "
                f"All participants must have unique titles."
            )
        participant = SequenceDiagramParticipant(title=title, sequence_ref=self)
        self.__participants.append(participant)
        return participant

    @contextmanager
    def loop(self, how_many_iterations: str) -> None:
        """
        Explicitly mark the following sequence of steps as performed in the loop
        """
        self.__add_step(
            LoopControl(is_active=True, how_many_iterations=how_many_iterations)
        )
        yield None
        self.__add_step(LoopControl(is_active=False))

    def return_(self, text: str = ""):
        if not self.auto_activation:
            raise AssertionError(
                "The method .return_() can be used only when diagram have been initialized"
                "with `auto_activation=True`. "
                "Please initialize the diagram with `auto_activation=True` "
                "or explicitly use .return_to() for the participant objects."
            )
        try:
            previously_active_participant = self.__auto_activation_stack[-1]
        except IndexError:
            raise AssertionError(
                "Sequence diagram stack does not hold the previous participant to return to. "
            )
        self.__add_step(
            ReturnStep(
                text,
                from_participant=previously_active_participant[1],
                to_participant=previously_active_participant[0],
            )
        )

    @contextmanager
    def group(self, text: str) -> None:
        """
        Explicitly mark the following sequence of steps as performed in the group
        """
        self.__add_step(GroupControl(is_active=True, text=text))
        yield None
        self.__add_step(GroupControl(is_active=False))

    @contextmanager
    def condition(self) -> None:
        """
        Explicitly mark the following sequence of steps as performed within some logical "if - else" block
        """
        self.__add_step(ConditionControl(is_active=True))
        self.__inside_condition = True
        yield
        self.__add_step(ConditionControl(is_active=False))
        self.__inside_condition = False

    @contextmanager
    def case(self, text: str):
        """
        Explicitly mark the following sequence of steps as performed within the specific condition
        """
        self.__add_step(CaseControl(is_active=True, text=text))
        yield
        self.__add_step(CaseControl(is_active=False))

    def __add_step(self, step: Step):
        if self.__inside_condition:
            # explicitly require the "CaseControl" to always happen right after the "ConditionControl"
            previous_step = self.__sequence[-1]
            if isinstance(previous_step, ConditionControl) and previous_step.is_active:
                if not isinstance(step, CaseControl):
                    raise AssertionError(
                        "After `with .condition()` the next step must be always `with .case()` "
                        "with the definition of the condition. "
                        "Please check the examples from the project repo."
                    )
        else:
            # do not allow "CaseControl" being used outside of the condition
            if isinstance(step, CaseControl):
                raise AssertionError(
                    "Context manager `with .case()` cannot be used separately outside of the "
                    "`with .condition()` context manager. "
                    "Please check the examples from the project repo."
                )

        if self.auto_activation:
            # If auto_activation is enabled,
            # every time we add a regular step transferring the control to another participant,
            # the activation must be started.
            # And every time the flow returns to the previously activated participant, its activation must be ended.

            if isinstance(step, ForwardStep):
                if not self.__auto_activation_stack:
                    # If stack is empty, the very first participant starting the flow must be activated as well.
                    self.__auto_activation_stack.append((None, step.from_participant))
                    self.__sequence.append(
                        ParticipantActivationControl(
                            is_active=True, participant=step.from_participant
                        )
                    )

                self.__sequence.append(step)

                if (
                    self.__auto_activation_stack
                    and step.to_participant != self.__auto_activation_stack[-1][-1]
                ):
                    # If the flow has been passed to the participant that is not currently considered as active,
                    # then activate it.
                    self.__auto_activation_stack.append(
                        (step.from_participant, step.to_participant)
                    )
                    self.__sequence.append(
                        ParticipantActivationControl(
                            is_active=True, participant=step.to_participant
                        )
                    )

            elif isinstance(step, ReturnStep):
                self.__sequence.append(step)

                if (
                    self.__auto_activation_stack
                    and (step.to_participant, step.from_participant)
                    == self.__auto_activation_stack[-1]
                ):
                    # If we are passing the flow back exactly to the participant,
                    # that previously has passed the control to us -
                    # deactivate the current participant.
                    self.__auto_activation_stack.pop()
                    self.__sequence.append(
                        ParticipantActivationControl(
                            is_active=False, participant=step.from_participant
                        )
                    )

                if self.__auto_activation_stack == [(None, step.to_participant)]:
                    # If we have returned back to the very first participant
                    # that has started the stack of the calls, then also deactivate it.
                    self.__auto_activation_stack.pop()
                    self.__sequence.append(
                        ParticipantActivationControl(
                            is_active=False, participant=step.to_participant
                        )
                    )
            else:
                self.__sequence.append(step)
        else:
            self.__sequence.append(step)

    def generate(self) -> str:
        return self.__generator.generate_sequence_diagram()

    def __repr__(self):
        return f"Sequence Diagram ({self.title})"