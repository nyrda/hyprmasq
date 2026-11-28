#include "HookChain.hpp"

#include <hyprland/src/plugins/HookSystem.hpp>

#include <sys/mman.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <expected>
#include <unordered_set>
#include <vector>

namespace hookchain {
    namespace {
        // Reaches private members of Hyprland's hook system: an explicit instantiation may name them. Each stops
        // compiling once Hyprland gives the member another type.
        template <typename Tag, typename Tag::type Member>
        struct SExpose {
            friend constexpr typename Tag::type exposed(Tag) {
                return Member;
            }
        };
        struct SHooks {
            using type = std::vector<UP<CFunctionHook>> CHookSystem::*;
            friend constexpr type                       exposed(SHooks);
        };
        struct SActiveHooks {
            using type = std::unordered_set<uint64_t> CHookSystem::*;
            friend constexpr type                     exposed(SActiveHooks);
        };
        struct SSource {
            using type = void*    CFunctionHook::*;
            friend constexpr type exposed(SSource);
        };
        struct SDestination {
            using type = void*    CFunctionHook::*;
            friend constexpr type exposed(SDestination);
        };
        struct SLaunch {
            using type = void*    CFunctionHook::*;
            friend constexpr type exposed(SLaunch);
        };
        struct SLand {
            using type = void*    CFunctionHook::*;
            friend constexpr type exposed(SLand);
        };
        struct SHookLen {
            using type = size_t   CFunctionHook::*;
            friend constexpr type exposed(SHookLen);
        };
        struct SOriginalBytes {
            using type = std::vector<unsigned char> CFunctionHook::*;
            friend constexpr type                   exposed(SOriginalBytes);
        };
        struct SActive {
            using type = bool     CFunctionHook::*;
            friend constexpr type exposed(SActive);
        };
        template struct SExpose<SHooks, &CHookSystem::m_hooks>;
        template struct SExpose<SActiveHooks, &CHookSystem::m_activeHooks>;
        template struct SExpose<SSource, &CFunctionHook::m_source>;
        template struct SExpose<SDestination, &CFunctionHook::m_destination>;
        template struct SExpose<SLaunch, &CFunctionHook::m_launchTrampolineAddr>;
        template struct SExpose<SLand, &CFunctionHook::m_landTrampolineAddr>;
        template struct SExpose<SHookLen, &CFunctionHook::m_hookLen>;
        template struct SExpose<SOriginalBytes, &CFunctionHook::m_originalBytes>;
        template struct SExpose<SActive, &CFunctionHook::m_active>;

        auto& hooksOf(CHookSystem& system) {
            return system.*exposed(SHooks{});
        }
        auto& activeHooksOf(CHookSystem& system) {
            return system.*exposed(SActiveHooks{});
        }
        void*& sourceOf(CFunctionHook& hook) {
            return hook.*exposed(SSource{});
        }
        void*& destinationOf(CFunctionHook& hook) {
            return hook.*exposed(SDestination{});
        }
        void*& launchOf(CFunctionHook& hook) {
            return hook.*exposed(SLaunch{});
        }
        void*& landOf(CFunctionHook& hook) {
            return hook.*exposed(SLand{});
        }
        size_t& hookLenOf(CFunctionHook& hook) {
            return hook.*exposed(SHookLen{});
        }
        auto& originalBytesOf(CFunctionHook& hook) {
            return hook.*exposed(SOriginalBytes{});
        }
        bool& activeOf(CFunctionHook& hook) {
            return hook.*exposed(SActive{});
        }

        // What CFunctionHook::hook() writes: `jmp rel32` over the function's first bytes, filled up with `nop`, and
        // `movabs rax, imm64; jmp rax` as the launch trampoline.
        constexpr unsigned char                 JUMP_REL  = 0xE9;
        constexpr size_t                        JUMP_SIZE = 5;
        constexpr unsigned char                 NOP       = 0x90;
        constexpr std::array<unsigned char, 12> THUNK{0x48, 0xB8, 0, 0, 0, 0, 0, 0, 0, 0, 0xFF, 0xE0};
        constexpr size_t                        THUNK_ADDRESS = 2;

        bool isThunk(const void* at) {
            const auto* code = static_cast<const unsigned char*>(at);
            return std::memcmp(code, THUNK.data(), THUNK_ADDRESS) == 0 &&
                std::memcmp(code + THUNK_ADDRESS + sizeof(void*), THUNK.data() + THUNK_ADDRESS + sizeof(void*), THUNK.size() - THUNK_ADDRESS - sizeof(void*)) == 0;
        }
        // Where a thunk, or the slot, jumps.
        void* addressIn(const void* thunk) {
            void* address = nullptr;
            std::memcpy(&address, static_cast<const unsigned char*>(thunk) + THUNK_ADDRESS, sizeof(address));
            return address;
        }
        void setAddressIn(void* thunk, void* address) {
            std::memcpy(static_cast<unsigned char*>(thunk) + THUNK_ADDRESS, &address, sizeof(address));
        }

        // The plugin's thunks: one page that stays readable, writable and executable, as Hyprland's trampolines are,
        // because another plugin's hook that leaves writes the address in the thunk of the hook before it.
        constexpr size_t  THUNK_SLOT    = 16;
        unsigned char*    thunkPage     = nullptr;
        size_t            thunkPageSize = 0;
        std::vector<bool> thunkUsed;

        void* allocateThunk() {
            if (!thunkPage) {
                const auto size = static_cast<size_t>(sysconf(_SC_PAGE_SIZE));
                void*      page = mmap(nullptr, size, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
                if (page == MAP_FAILED)
                    return nullptr;
                thunkPage     = static_cast<unsigned char*>(page);
                thunkPageSize = size;
                thunkUsed.assign(size / THUNK_SLOT, false);
            }
            const auto free = std::ranges::find(thunkUsed, false);
            if (free == thunkUsed.end())
                return nullptr;
            *free = true;
            return thunkPage + (free - thunkUsed.begin()) * THUNK_SLOT;
        }
        void freeThunk(void* thunk) {
            const auto* at = static_cast<unsigned char*>(thunk);
            if (thunkPage && at >= thunkPage && at < thunkPage + thunkPageSize)
                thunkUsed[(at - thunkPage) / THUNK_SLOT] = false;
        }

        // The hooks of one function, as Hyprland's hook system and the code tell.
        struct SChain {
            void*                       source = nullptr;
            void*                       slot   = nullptr; // names the destination of the first hook
            void*                       end    = nullptr; // the function's first instructions, and a jump to the rest
            std::vector<unsigned char>  original;         // the function's first bytes
            std::vector<CFunctionHook*> order;            // as a call runs through them
        };

        // The slot that the code at source jumps to, if it is what hook() writes there.
        void* slotOf(void* source) {
            const auto* code = static_cast<const unsigned char*>(source);
            if (code[0] != JUMP_REL)
                return nullptr;
            int32_t offset = 0;
            std::memcpy(&offset, code + 1, sizeof(offset));
            return const_cast<unsigned char*>(code) + JUMP_SIZE + offset;
        }

        // Where a hook of the chain goes on: the destination of the next hook, or the chain's end.
        void* nextOf(CFunctionHook& hook) {
            return hook.m_original == landOf(hook) ? landOf(hook) : addressIn(hook.m_original);
        }

        std::expected<SChain, std::string> read(void* source) {
            auto& system = *g_pFunctionHookSystem;
            if (!activeHooksOf(system).contains(reinterpret_cast<uint64_t>(source)))
                return std::unexpected("no plugin hooks it");
            SChain chain;
            chain.source = source;
            chain.slot   = slotOf(source);
            if (!chain.slot)
                return std::unexpected("its first bytes are not the jump of Hyprland's hook system");
            // Every hook of the function names the same slot and end.
            std::vector<CFunctionHook*> hooks;
            for (const auto& hook : hooksOf(system)) {
                if (hook && activeOf(*hook) && sourceOf(*hook) == source && launchOf(*hook) == chain.slot)
                    hooks.push_back(hook.get());
            }
            if (hooks.empty())
                return std::unexpected("Hyprland's hook system has no hook that its first bytes jump to");
            auto& first    = *hooks.front();
            chain.end      = landOf(first);
            chain.original = originalBytesOf(first);
            const auto len = hookLenOf(first);
            if (!chain.end || len < JUMP_SIZE || chain.original.size() != len)
                return std::unexpected("its hook does not hold the function's first bytes");
            const auto* code = static_cast<const unsigned char*>(source);
            if (std::ranges::any_of(code + JUMP_SIZE, code + len, [](unsigned char byte) { return byte != NOP; }))
                return std::unexpected("its first bytes are not the jump of Hyprland's hook system");
            if (!isThunk(chain.slot))
                return std::unexpected("the code that its first bytes jump to is not of Hyprland's hook system");
            void* destination = addressIn(chain.slot);
            for (size_t step = 0; step <= hooks.size(); ++step) {
                const auto found = std::ranges::find_if(hooks, [&](CFunctionHook* hook) { return destinationOf(*hook) == destination && landOf(*hook) == chain.end; });
                if (found == hooks.end() || std::ranges::contains(chain.order, *found))
                    return std::unexpected("one of its hooks is not of Hyprland's hook system");
                auto& hook = **found;
                chain.order.push_back(&hook);
                if (hook.m_original == chain.end)
                    return chain;
                if (!hook.m_original || !isThunk(hook.m_original))
                    return std::unexpected("one of its hooks does not go on as hooks that share a function do");
                destination = addressIn(hook.m_original);
                if (destination == chain.end)
                    return chain;
            }
            return std::unexpected("its hooks do not end");
        }

        // Writes the function's first bytes back and takes it out of the hooked functions, as CFunctionHook::unhook()
        // does. Should the function's code not take the write, the slot is made to jump to the end, so that the
        // function runs as Hyprland built it through its jump, and it stays among the hooked functions, which keeps a
        // hook() from moving that jump.
        void restore(void* source, const std::vector<unsigned char>& original, void* slot, void* end) {
            const auto page   = static_cast<uintptr_t>(sysconf(_SC_PAGE_SIZE));
            const auto first  = reinterpret_cast<uintptr_t>(source) / page * page;
            const auto length = (reinterpret_cast<uintptr_t>(source) + original.size() - first + page - 1) / page * page;
            if (mprotect(reinterpret_cast<void*>(first), length, PROT_READ | PROT_WRITE | PROT_EXEC) != 0) {
                if (slot && end && isThunk(slot))
                    setAddressIn(slot, end);
                return;
            }
            std::memcpy(source, original.data(), original.size());
            mprotect(reinterpret_cast<void*>(first), length, PROT_READ | PROT_EXEC);
            activeHooksOf(*g_pFunctionHookSystem).erase(reinterpret_cast<uint64_t>(source));
        }

        // Takes a hook out of the hooks of a function that an unhook() has put back. Those still name each other, from
        // the slot on. A plugin that puts the function's jump back to the slot of its own first hook() finds the slot
        // as it left it then, and no slot or thunk goes on naming the code of a hook that has left.
        void unlinkDropped(CFunctionHook& hook) {
            void* at = launchOf(hook);
            if (!at || !hook.m_original)
                return;
            const auto& hooks = hooksOf(*g_pFunctionHookSystem);
            for (size_t step = 0; step <= hooks.size(); ++step) {
                if (!isThunk(at))
                    return;
                void* const destination = addressIn(at);
                if (destination == destinationOf(hook)) {
                    setAddressIn(at, nextOf(hook));
                    return;
                }
                const auto before = std::ranges::find_if(hooks, [&](const auto& other) {
                    return other && other.get() != &hook && activeOf(*other) && launchOf(*other) == launchOf(hook) && destinationOf(*other) == destination && other->m_original &&
                        other->m_original != landOf(*other);
                });
                if (before == hooks.end())
                    return;
                at = (*before)->m_original;
            }
        }

        // Leaves the CFunctionHook as unhook() does, and frees its thunk.
        void reset(CFunctionHook& hook) {
            if (hook.m_original != landOf(hook))
                freeThunk(hook.m_original);
            activeOf(hook)  = false;
            hookLenOf(hook) = 0;
            landOf(hook)    = nullptr;
            launchOf(hook)  = nullptr;
            hook.m_original = nullptr;
            originalBytesOf(hook).clear();
        }
    }

    bool hooked(void* source) {
        return g_pFunctionHookSystem && activeHooksOf(*g_pFunctionHookSystem).contains(reinterpret_cast<uint64_t>(source));
    }

    std::optional<std::string> refusal(void* source) {
        if (const auto chain = read(source); !chain)
            return chain.error();
        return std::nullopt;
    }

    std::optional<std::string> join(CFunctionHook& hook) {
        if (activeOf(hook))
            return "the hook is in already";
        const auto chain = read(sourceOf(hook));
        if (!chain)
            return chain.error();
        // Also a hook that an unhook() dropped and that did not leave since: the function's hooks are told apart by the
        // code that they call.
        if (std::ranges::any_of(hooksOf(*g_pFunctionHookSystem), [&](const auto& other) {
                return other && other.get() != &hook && activeOf(*other) && sourceOf(*other) == sourceOf(hook) && launchOf(*other) == chain->slot &&
                    destinationOf(*other) == destinationOf(hook);
            }))
            return "one of its hooks calls the same code";
        void* thunk = allocateThunk();
        if (!thunk)
            return "no memory for the hook";
        std::memcpy(thunk, THUNK.data(), THUNK.size());
        setAddressIn(thunk, addressIn(chain->slot));
        launchOf(hook)        = chain->slot;
        landOf(hook)          = chain->end;
        hookLenOf(hook)       = chain->original.size();
        originalBytesOf(hook) = chain->original;
        activeOf(hook)        = true;
        hook.m_original       = thunk;
        // From here calls run through the hook.
        setAddressIn(chain->slot, destinationOf(hook));
        return std::nullopt;
    }

    eLeft leave(CFunctionHook& hook) {
        if (!activeOf(hook))
            return eLeft::NOT_INSTALLED;
        void* const source = sourceOf(hook);
        // An unhook() of another hook of the function put its first bytes back, and a hook() since then has a slot of
        // its own: either way no call reaches this hook.
        if (!hooked(source) || slotOf(source) != launchOf(hook)) {
            unlinkDropped(hook);
            reset(hook);
            return eLeft::DROPPED;
        }
        const auto chain = read(source);
        if (!chain) {
            // The function jumps to this hook's slot, and what follows cannot be told, so nothing can be said to go on
            // without this hook's code. The function's first bytes are right in every hook.
            restore(source, originalBytesOf(hook), launchOf(hook), landOf(hook));
            reset(hook);
            return eLeft::RESET;
        }
        const auto self = std::ranges::find(chain->order, &hook);
        if (self == chain->order.end()) {
            reset(hook);
            return eLeft::DROPPED;
        }
        void* const next = nextOf(hook);
        if (self == chain->order.begin()) {
            if (next == chain->end) {
                restore(source, chain->original, chain->slot, chain->end);
                reset(hook);
                return eLeft::LAST;
            }
            setAddressIn(chain->slot, next);
        } else
            // The hook before this one joined, or it would be the last, so it has a thunk.
            setAddressIn((*(self - 1))->m_original, next);
        reset(hook);
        return eLeft::SPLICED;
    }

    bool linked(CFunctionHook& hook) {
        if (!activeOf(hook))
            return false;
        const auto chain = read(sourceOf(hook));
        return chain && std::ranges::contains(chain->order, &hook);
    }

    void* next(CFunctionHook& hook) {
        return linked(hook) ? hook.m_original : sourceOf(hook);
    }

    size_t members(void* source) {
        if (!hooked(source))
            return 0;
        const auto chain = read(source);
        return chain ? chain->order.size() : 0;
    }

    void shutdown() {
        // A thunk that is still in use, which a hook of the plugin that did not leave would have, keeps the page.
        if (!thunkPage || std::ranges::contains(thunkUsed, true))
            return;
        munmap(thunkPage, thunkPageSize);
        thunkPage     = nullptr;
        thunkPageSize = 0;
        thunkUsed.clear();
    }
}
