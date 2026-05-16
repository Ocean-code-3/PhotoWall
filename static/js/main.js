const carousels = document.querySelectorAll("[data-carousel]");

carousels.forEach((carousel) => {
    const slides = Array.from(carousel.querySelectorAll(".carousel-slide"));
    const dots = Array.from(carousel.querySelectorAll("[data-carousel-dot]"));

    if (slides.length <= 1) {
        return;
    }

    let activeIndex = 0;

    function showSlide(index) {
        activeIndex = (index + slides.length) % slides.length;
        carousel.style.setProperty("--active-slide", activeIndex);

        slides.forEach((slide, slideIndex) => {
            slide.classList.toggle("is-active", slideIndex === activeIndex);
        });

        dots.forEach((dot, dotIndex) => {
            dot.classList.toggle("is-active", dotIndex === activeIndex);
        });
    }

    let timer = window.setInterval(() => {
        showSlide(activeIndex + 1);
    }, 4200);

    dots.forEach((dot, dotIndex) => {
        dot.addEventListener("click", () => {
            window.clearInterval(timer);
            showSlide(dotIndex);
            timer = window.setInterval(() => {
                showSlide(activeIndex + 1);
            }, 4200);
        });
    });
});
