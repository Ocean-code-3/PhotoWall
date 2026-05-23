const carousels = document.querySelectorAll("[data-carousel]");

carousels.forEach((carousel) => {
    const slides = Array.from(carousel.querySelectorAll(".carousel-slide"));
    const dots = Array.from(carousel.querySelectorAll("[data-carousel-dot]"));
    const stayDuration = 2000;
    const fadeDuration = 1700;

    if (slides.length <= 1) {
        return;
    }

    let activeIndex = 0;

    let exitTimer;

    function showSlide(index) {
        const nextIndex = (index + slides.length) % slides.length;
        const previousIndex = activeIndex;

        if (nextIndex === activeIndex) {
            return;
        }

        window.clearTimeout(exitTimer);

        slides.forEach((slide, slideIndex) => {
            slide.classList.toggle("is-active", slideIndex === nextIndex);
            slide.classList.toggle("is-exiting", slideIndex === previousIndex);
        });

        dots.forEach((dot, dotIndex) => {
            dot.classList.toggle("is-active", dotIndex === nextIndex);
        });

        activeIndex = nextIndex;
        exitTimer = window.setTimeout(() => {
            slides.forEach((slide) => {
                slide.classList.remove("is-exiting");
            });
        }, fadeDuration);
    }

    let timer = window.setInterval(() => {
        showSlide(activeIndex + 1);
    }, stayDuration + fadeDuration);

    dots.forEach((dot, dotIndex) => {
        dot.addEventListener("click", () => {
            window.clearInterval(timer);
            showSlide(dotIndex);
            timer = window.setInterval(() => {
                showSlide(activeIndex + 1);
            }, stayDuration + fadeDuration);
        });
    });
});

const uploadForms = document.querySelectorAll("[data-compress-upload]");
const uploadMaxEdge = 2400;
const uploadQuality = 0.86;
const uploadCompressThreshold = 1.2 * 1024 * 1024;

function formatFileSize(bytes) {
    if (bytes >= 1024 * 1024) {
        return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    }

    return `${Math.round(bytes / 1024)} KB`;
}

function setUploadStatus(form, message) {
    const status = form.querySelector("[data-upload-status]");
    if (status) {
        status.textContent = message;
    }
}

function loadImageFile(file) {
    return new Promise((resolve, reject) => {
        const image = new Image();
        const objectUrl = URL.createObjectURL(file);

        image.onload = () => {
            URL.revokeObjectURL(objectUrl);
            resolve(image);
        };

        image.onerror = () => {
            URL.revokeObjectURL(objectUrl);
            reject(new Error("Image load failed"));
        };

        image.src = objectUrl;
    });
}

async function compressUploadImage(file) {
    if (!file.type.startsWith("image/") || file.type === "image/gif") {
        return file;
    }

    const image = await loadImageFile(file);
    const scale = Math.min(1, uploadMaxEdge / Math.max(image.naturalWidth, image.naturalHeight));

    if (scale === 1 && file.size < uploadCompressThreshold) {
        return file;
    }

    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));

    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    const blob = await new Promise((resolve) => {
        canvas.toBlob(resolve, "image/jpeg", uploadQuality);
    });

    if (!blob || blob.size >= file.size) {
        return file;
    }

    const newName = file.name.replace(/\.[^.]+$/, "") || "photo";
    return new File([blob], `${newName}.jpg`, {
        type: "image/jpeg",
        lastModified: Date.now(),
    });
}

uploadForms.forEach((form) => {
    form.addEventListener("submit", async (event) => {
        if (form.dataset.uploadReady === "1") {
            return;
        }

        const input = form.querySelector('input[type="file"][name="photo"]');
        const submitButton = form.querySelector('button[type="submit"]');
        const file = input?.files?.[0];

        if (!input || !file || !window.DataTransfer) {
            return;
        }

        event.preventDefault();
        submitButton.disabled = true;
        setUploadStatus(form, "正在优化照片，请稍等...");

        try {
            const compressedFile = await compressUploadImage(file);
            const transfer = new DataTransfer();
            transfer.items.add(compressedFile);
            input.files = transfer.files;

            if (compressedFile.size < file.size) {
                setUploadStatus(
                    form,
                    `已从 ${formatFileSize(file.size)} 压缩到 ${formatFileSize(compressedFile.size)}，正在上传...`
                );
            } else {
                setUploadStatus(form, "照片无需压缩，正在上传...");
            }

            form.dataset.uploadReady = "1";
            form.requestSubmit();
        } catch (error) {
            setUploadStatus(form, "照片优化失败，将上传原图...");
            form.dataset.uploadReady = "1";
            form.requestSubmit();
        }
    });
});
