from base_classes import InteractionAction


class DebugReloadAction(InteractionAction):
    """
    Reload dynamic components without restarting the app.

    Clears ComponentRegistry caches so actions and contexts are re-imported
    from disk on next use. Also clears the prompt resolver cache.

    Usage: run the user command 'debug reload'.
    """

    def __init__(self, session):
        self.session = session

    def run(self, args=None):
        registry = getattr(self.session, "_registry", None)
        if registry is None:
            print("Registry not available; cannot reload")
            return

        # Clear action class cache
        action_count = len(getattr(registry, "_action_cache", {}))
        try:
            registry._action_cache.clear()
        except Exception as e:
            print(f"Error clearing action cache: {e}")

        # Clear loaded context classes
        context_count = len(getattr(registry, "_context_classes", {}))
        try:
            registry._context_classes.clear()
            # Re-scan contexts directory for fresh classes on demand
            # Actual (re)loading occurs lazily when contexts are requested.
        except Exception as e:
            print(f"Error clearing context classes: {e}")

        # Clear prompt resolver cache if available
        prompt_cleared = False
        try:
            pr = registry.get_prompt_resolver()
            if hasattr(pr, "_cache"):
                pr._cache.clear()
                prompt_cleared = True
        except Exception:
            pass

        print(
            "Reloaded: "
            f"actions={action_count} contexts={context_count}"
            + (" prompts=cleared" if prompt_cleared else "")
        )
        print("Changes will apply on next use of each component.")

