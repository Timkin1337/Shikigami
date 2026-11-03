package dev.shikigami.eyes;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.mojang.logging.LogUtils;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.chat.Component;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.monster.Creeper;
import net.minecraft.world.entity.monster.Enemy;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.fml.common.Mod;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import org.slf4j.Logger;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * «Глаза Shikigami»: клиентский мод, который раз в полсекунды рассказывает Shikigami (ассистенту на этом ПК), что видит игрок.
 * Ничего не меняет в игре и не добавляет блоков — поэтому ставится в любую сборку и на любой сервер.
 * Данные уходят только на этот компьютер: http://127.0.0.1:7853 (адрес можно сменить переменной окружения SHIKIGAMI_URL).
 */
@Mod(value = ShikigamiEyes.MODID, dist = Dist.CLIENT)
@EventBusSubscriber(modid = ShikigamiEyes.MODID, value = Dist.CLIENT)
public class ShikigamiEyes {
    public static final String MODID = "shikigami_eyes";
    private static final Logger LOG = LogUtils.getLogger();
    private static final String URL = System.getenv().getOrDefault("SHIKIGAMI_URL", "http://127.0.0.1:7853") + "/api/plugins/minecraft/eyes";
    private static final int EVERY_TICKS = 10;          // 0,5 с
    private static final double RADIUS = 24;
    private static final int MAX_NEAR = 12;

    private static final HttpClient HTTP = HttpClient.newBuilder().connectTimeout(Duration.ofMillis(400)).build();
    private static final AtomicBoolean busy = new AtomicBoolean(false);
    private static long ticks = 0;
    private static long quietUntil = 0;                 // Shikigami не отвечает — не стучимся каждые полсекунды
    private static boolean wasInWorld = false;
    private static boolean warned = false;

    public ShikigamiEyes() {
        LOG.info("Глаза Shikigami: отправляю состояние игры на {}", URL);
    }

    @SubscribeEvent
    static void onTick(ClientTickEvent.Post event) {
        if (++ticks % EVERY_TICKS != 0) return;
        long now = System.currentTimeMillis();
        if (now < quietUntil || busy.get()) return;
        Minecraft mc = Minecraft.getInstance();
        JsonObject state;
        try {
            if (mc.player == null || mc.level == null) {
                if (!wasInWorld) return;
                wasInWorld = false;
                state = new JsonObject();
                state.addProperty("v", 1);
                state.addProperty("in_world", false);
            } else {
                wasInWorld = true;
                state = snapshot(mc, mc.player, mc.level);
            }
        } catch (Throwable t) {          // мод не должен ронять игру ни при каком раскладе
            if (!warned) { LOG.warn("Глаза Shikigami: не удалось собрать состояние", t); warned = true; }
            return;
        }
        send(state.toString());
    }

    private static void send(String body) {
        busy.set(true);
        HttpRequest req = HttpRequest.newBuilder(URI.create(URL))
                .timeout(Duration.ofMillis(800))
                .header("Content-Type", "application/json; charset=utf-8")
                .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
                .build();
        HTTP.sendAsync(req, HttpResponse.BodyHandlers.discarding()).whenComplete((resp, err) -> {
            busy.set(false);
            if (err != null || resp.statusCode() >= 400) quietUntil = System.currentTimeMillis() + 10_000;
        });
    }

    // ---------------- что видит игрок ----------------

    private static JsonObject snapshot(Minecraft mc, LocalPlayer p, Level level) {
        JsonObject o = new JsonObject();
        o.addProperty("v", 1);
        o.addProperty("in_world", true);
        o.addProperty("paused", mc.isPaused());
        o.addProperty("player", p.getName().getString());
        o.addProperty("single", mc.isSingleplayer());
        o.addProperty("mode", mc.gameMode != null ? mc.gameMode.getPlayerMode().getName() : "?");

        // где
        BlockPos pos = p.blockPosition();
        o.addProperty("dim", level.dimension().toString());
        var biome = level.getBiome(pos);
        String biomeId = biome.getRegisteredName();
        o.addProperty("biome", biomeId);
        o.addProperty("biome_name", Component.translatable("biome." + biomeId.replace(':', '.')).getString());
        JsonArray xyz = new JsonArray();
        xyz.add(Math.round(p.getX())); xyz.add(Math.round(p.getY())); xyz.add(Math.round(p.getZ()));
        o.add("pos", xyz);
        o.addProperty("yaw", Math.round(p.getYRot()));
        o.addProperty("pitch", Math.round(p.getXRot()));
        o.addProperty("day_time", level.getDayTime());
        o.addProperty("rain", level.isRaining());
        o.addProperty("thunder", level.isThundering());
        o.addProperty("light", level.getMaxLocalRawBrightness(pos));
        o.addProperty("sky", level.canSeeSky(pos));

        // сам игрок
        o.addProperty("hp", round1(p.getHealth()));
        o.addProperty("hp_max", round1(p.getMaxHealth()));
        o.addProperty("food", p.getFoodData().getFoodLevel());
        o.addProperty("sat", round1(p.getFoodData().getSaturationLevel()));
        o.addProperty("air", p.getAirSupply());
        o.addProperty("air_max", p.getMaxAirSupply());
        o.addProperty("armor", p.getArmorValue());
        o.addProperty("level", p.experienceLevel);
        o.addProperty("dead", p.isDeadOrDying());
        o.addProperty("fire", p.isOnFire());
        o.addProperty("water", p.isInWater());
        o.addProperty("lava", p.isInLava());
        o.addProperty("sleeping", p.isSleeping());
        o.addProperty("sprint", p.isSprinting());
        o.addProperty("sneak", p.isShiftKeyDown());
        o.addProperty("swim", p.isSwimming());
        o.addProperty("elytra", p.isFallFlying());
        o.addProperty("ground", p.onGround());
        o.addProperty("fall", round1(p.fallDistance));
        o.addProperty("using", p.isUsingItem());
        o.addProperty("mining", mc.gameMode != null && mc.gameMode.isDestroying());
        Entity vehicle = p.getVehicle();
        if (vehicle != null) o.addProperty("vehicle", vehicle.getName().getString());

        o.add("hand", item(p.getMainHandItem()));
        o.add("offhand", item(p.getOffhandItem()));
        o.add("inv", inventory(p));

        JsonArray effects = new JsonArray();
        for (MobEffectInstance e : p.getActiveEffects()) {
            JsonObject j = new JsonObject();
            j.addProperty("name", e.getEffect().value().getDisplayName().getString());
            j.addProperty("lvl", e.getAmplifier() + 1);
            j.addProperty("sec", e.getDuration() / 20);
            effects.add(j);
        }
        o.add("effects", effects);

        DamageSource dmg = p.getLastDamageSource();     // на клиенте живёт ~2 с после удара
        if (dmg != null) {
            JsonObject j = new JsonObject();
            j.addProperty("type", dmg.getMsgId());
            if (dmg.getEntity() != null) j.addProperty("by", dmg.getEntity().getName().getString());
            o.add("damage", j);
        }

        o.add("near", nearby(p, level));
        o.add("look", looking(mc, level));
        if (mc.screen != null) {
            JsonObject s = new JsonObject();
            s.addProperty("cls", mc.screen.getClass().getSimpleName());
            s.addProperty("title", mc.screen.getTitle().getString());
            o.add("screen", s);
        }
        return o;
    }

    private static double round1(double v) { return Math.round(v * 10) / 10.0; }

    private static JsonObject item(ItemStack st) {
        JsonObject j = new JsonObject();
        if (st.isEmpty()) return j;
        j.addProperty("id", BuiltInRegistries.ITEM.getKey(st.getItem()).toString());
        j.addProperty("name", st.getHoverName().getString());
        j.addProperty("count", st.getCount());
        if (st.isDamageableItem()) {
            j.addProperty("dur", st.getMaxDamage() - st.getDamageValue());
            j.addProperty("dur_max", st.getMaxDamage());
        }
        return j;
    }

    /** Инвентарь целиком (вместе с бронёй и второй рукой), одинаковые предметы сложены. */
    private static JsonArray inventory(Player p) {
        Map<String, JsonObject> sum = new LinkedHashMap<>();
        var inv = p.getInventory();
        for (int i = 0; i < inv.getContainerSize(); i++) {
            ItemStack st = inv.getItem(i);
            if (st.isEmpty()) continue;
            String id = BuiltInRegistries.ITEM.getKey(st.getItem()).toString();
            String name = st.getHoverName().getString();
            JsonObject j = sum.computeIfAbsent(id + "|" + name, k -> {
                JsonObject n = new JsonObject();
                n.addProperty("id", id);
                n.addProperty("name", name);
                n.addProperty("count", 0);
                return n;
            });
            j.addProperty("count", j.get("count").getAsInt() + st.getCount());
        }
        JsonArray a = new JsonArray();
        sum.values().forEach(a::add);
        return a;
    }

    /** Кто рядом: ближайшие существа с направлением относительно взгляда игрока. */
    private static JsonArray nearby(LocalPlayer p, Level level) {
        List<Entity> list = new ArrayList<>();
        for (Entity e : level.getEntities(p, p.getBoundingBox().inflate(RADIUS))) {
            if (e instanceof LivingEntity && e.isAlive() && !e.isInvisible()) list.add(e);
        }
        list.sort(Comparator.comparingDouble(e -> e.distanceToSqr(p)));
        Vec3 me = p.position();
        double yaw = Math.toRadians(p.getYRot());
        double fx = -Math.sin(yaw), fz = Math.cos(yaw);              // куда смотрит игрок (в плоскости)
        JsonArray a = new JsonArray();
        for (Entity e : list.subList(0, Math.min(MAX_NEAR, list.size()))) {
            JsonObject j = new JsonObject();
            j.addProperty("id", BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString());
            j.addProperty("name", e.getName().getString());
            double dx = e.getX() - me.x, dz = e.getZ() - me.z;
            j.addProperty("dist", round1(Math.sqrt(e.distanceToSqr(p))));
            j.addProperty("dy", Math.round(e.getY() - me.y));
            double len = Math.max(1e-6, Math.hypot(dx, dz));
            double ang = Math.toDegrees(Math.atan2(fx * (dz / len) - fz * (dx / len), fx * (dx / len) + fz * (dz / len)));
            j.addProperty("angle", Math.round(ang));               // >0 — справа, <0 — слева, ±180 — сзади
            j.addProperty("hostile", e instanceof Enemy);
            j.addProperty("player", e instanceof Player);
            if (e instanceof LivingEntity le) j.addProperty("hp", round1(le.getHealth()));
            if (e instanceof Creeper c) j.addProperty("swell", round1(c.getSwelling(1f)));
            a.add(j);
        }
        return a;
    }

    private static JsonObject looking(Minecraft mc, Level level) {
        JsonObject j = new JsonObject();
        HitResult hr = mc.hitResult;
        if (hr == null || hr.getType() == HitResult.Type.MISS || mc.player == null) return j;
        j.addProperty("dist", round1(hr.getLocation().distanceTo(mc.player.getEyePosition())));
        if (hr instanceof BlockHitResult bh) {
            var state = level.getBlockState(bh.getBlockPos());
            j.addProperty("kind", "block");
            j.addProperty("id", BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString());
            j.addProperty("name", state.getBlock().getName().getString());
        } else if (hr instanceof EntityHitResult eh) {
            j.addProperty("kind", "entity");
            j.addProperty("id", BuiltInRegistries.ENTITY_TYPE.getKey(eh.getEntity().getType()).toString());
            j.addProperty("name", eh.getEntity().getName().getString());
        }
        return j;
    }
}
