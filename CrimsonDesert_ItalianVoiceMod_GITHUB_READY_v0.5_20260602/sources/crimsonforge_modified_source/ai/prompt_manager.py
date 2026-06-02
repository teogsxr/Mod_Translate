"""Translation prompt template manager.

Manages system and user prompts for AI translation.
All prompts are user-customizable via settings.
"""

from utils.logger import get_logger

logger = get_logger("ai.prompt_manager")


class PromptManager:
    """Manages AI translation prompts from config."""

    def __init__(self, system_prompt_template: str = "", user_prompt_template: str = ""):
        self._default_system_template = (
            "You are an expert game localization translator specializing in AAA fantasy RPG titles. "
            "Your task is to translate game UI text from {source_lang} to {target_lang} with production-quality accuracy.\n\n"
            "TRANSLATION RULES:\n"
            "1. Translate ONLY the human-readable text. Never modify, remove, or translate any of the following:\n"
            "   - HTML tags (<br/>, <b>, <i>, <span>, <div>, etc.)\n"
            "   - CSS class names or inline styles\n"
            "   - Template variables ({{0}}, {{1}}, {{variable_name}}, %s, %d, etc.)\n"
            "   - Color codes (#FFFFFF, rgb(), etc.)\n"
            "   - Game-specific markup or escape sequences\n"
            "   - File paths, URLs, or asset references\n"
            "   - Numeric stat values, damage numbers, item IDs\n"
            "2. Maintain the exact same number of format placeholders in the output as in the input.\n"
            "3. Preserve line breaks and whitespace structure exactly as provided.\n"
            "4. Use vocabulary and tone consistent with high-fantasy RPG games: medieval, mythical, epic, immersive.\n"
            "5. Adapt idioms and expressions to feel native in {target_lang} rather than doing literal word-for-word translation.\n"
            "6. For character names, place names, and proper nouns: keep them untranslated unless there is an established official localization.\n"
            "7. For UI labels (button text, menu items, stat names): use the standard accepted gaming terminology in {target_lang}.\n"
            "8. Match the register of the original: if formal dialogue, keep formal. If casual banter, keep casual.\n"
            "9. If the source text is a single word or short label, return only the equivalent single word or short label.\n"
            "10. Return ONLY the translated text. No explanations, no notes, no alternatives, no quotation marks wrapping the result."
        )
        self._default_user_template = "{text}"
        self._system_template = system_prompt_template or self._default_system_template
        self._user_template = user_prompt_template or self._default_user_template

    def get_system_prompt(self, source_lang: str, target_lang: str,
                          glossary_text: str = "", **kwargs) -> str:
        """Build the system prompt with language substitution and optional glossary.

        Args:
            source_lang: Source language name.
            target_lang: Target language name.
            glossary_text: Optional glossary string to append to the prompt.
        """
        prompt = self._system_template.format(
            source_lang=source_lang,
            target_lang=target_lang,
            **kwargs,
        )
        if glossary_text:
            prompt += "\n\n" + glossary_text
        return prompt

    def get_user_prompt(self, text: str, **kwargs) -> str:
        """Build the user prompt with text substitution."""
        return self._user_template.format(text=text, **kwargs)

    @property
    def system_template(self) -> str:
        return self._system_template

    @system_template.setter
    def system_template(self, value: str):
        self._system_template = value

    @property
    def user_template(self) -> str:
        return self._user_template

    @user_template.setter
    def user_template(self, value: str):
        self._user_template = value

    def update_from_config(self, config: dict) -> None:
        """Update prompts from the translation config section."""
        self._system_template = config.get("system_prompt") or self._default_system_template
        self._user_template = config.get("user_prompt_template") or self._default_user_template
